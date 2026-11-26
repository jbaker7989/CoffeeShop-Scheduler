"""Real PostgreSQL, ADK process, A2A and LangGraph integration for CS-011/015."""
import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from threading import Barrier
from uuid import UUID, uuid4

import httpx
import psycopg
from a2a.client.transports.jsonrpc import JsonRpcTransport
from a2a.types import Message, SendMessageRequest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat
from test_cs011_agent_contracts import message, token

from agents.a2a_transport import AgentError, ServiceIdentity
from agents.demand_forecast.model import persist_forecasts
from agents.demand_forecast.service import forecast_source, score_forecast
from agents.scheduler.draft_runs import DraftRuns
from agents.scheduler.inputs import MissingInputError, assemble_solver_inputs
from contracts.forecast import PINNED_TAXONOMY_VERSION, from_json
from data_access.session import tenant_session

DSN = os.environ.get("CS015_TEST_DATABASE_URL")
ADK = os.environ.get("CS011_ADK_PYTHON")
TENANT = UUID("00000000-0000-0000-0000-000000000001")
ROOT = Path(__file__).resolve().parents[1]
DAY = date(2026, 12, 7)


@contextmanager
def running_agent(python, name, settings):
    with socket.socket() as port_holder:
        port_holder.bind(("127.0.0.1", 0))
        port = port_holder.getsockname()[1]
    url = f"http://127.0.0.1:{port}/a2a"
    with tempfile.TemporaryFile(mode="w+b") as log, subprocess.Popen(
        [python, "-m", "agents.serve", name, "--port", str(port)], cwd=ROOT,
        env={**os.environ, **settings, "A2A_ENDPOINT": url, "A2A_LOOPBACK_TEST": "1"},
        stdout=log, stderr=log,
    ) as process:
        try:
            for _ in range(100):
                if process.poll() is not None:
                    break
                try:
                    response = httpx.get(url.removesuffix("/a2a") + "/.well-known/agent-card.json", timeout=0.2)
                    response.raise_for_status()
                    if response.json()["name"] == name:
                        break
                except httpx.HTTPError:
                    time.sleep(0.1)
            else:
                raise AssertionError(f"{name} did not become ready")
            if process.poll() is not None:
                log.seek(0)
                raise AssertionError(log.read().decode(errors="replace"))
            yield url
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


@unittest.skipUnless(DSN, "CS015_TEST_DATABASE_URL must target a migrated, seeded disposable PostgreSQL")
class DurableWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tenant = uuid4()
        self.identity = ServiceIdentity(self.tenant, "test-agent", "test-issuer")
        with psycopg.connect(DSN) as conn:
            conn.execute("INSERT INTO tenants (id, slug, name, status) VALUES (%s,%s,'Synthetic workflow','active')",
                         (self.tenant, f"test-{self.tenant}"))
        self.addCleanup(self._remove_tenant)

    def _remove_tenant(self):
        with psycopg.connect(DSN) as conn:
            conn.execute("DELETE FROM tenants WHERE id = %s", (self.tenant,))

    # spec: F12.ac7 — A fresh repository validates ownership and fences a stale attempt.
    def test_checkpoint_resume_and_fence(self):
        run_id = uuid4()
        repo = DraftRuns(DSN)
        request = {"version": 1, "start": str(DAY), "end": str(DAY)}
        claim = repo.claim(self.identity, run_id, request, lease_seconds=90)
        with self.assertRaisesRegex(AgentError, "draft_busy"):
            DraftRuns(DSN).claim(self.identity, run_id, request, lease_seconds=90)
        repo.save(self.identity, claim, "gather", {"pins": {"fixture": "v1"}})
        repo.save(self.identity, claim, "forecast", {"pins": {"fixture": "v1"}, "forecast": {"p70": "1.50"}})

        with tenant_session(DSN, role="app_rw", tenant_id=self.tenant) as conn:
            conn.execute("UPDATE schedule_draft_runs SET lease_until = '-infinity' WHERE id = %s", (run_id,))
        with self.assertRaisesRegex(AgentError, "draft_unavailable"):
            repo.claim(ServiceIdentity(self.tenant, "other-owner", "test-issuer"), run_id, request, lease_seconds=90)
        with self.assertRaisesRegex(AgentError, "draft_conflict"):
            repo.claim(self.identity, run_id, {**request, "end": "2026-12-08"}, lease_seconds=90)
        fresh = DraftRuns(DSN).claim(self.identity, run_id, request, lease_seconds=90)
        self.assertEqual(fresh.state["forecast"]["p70"], "1.50")
        self.assertNotEqual(fresh.attempt, claim.attempt)
        result = {"run_id": str(run_id), "version": 1, "status": "draft"}
        with self.assertRaisesRegex(AgentError, "draft_claim_lost"):
            repo.save(self.identity, claim, "done", {"result": result})
        repo.save(self.identity, fresh, "done", {"result": result})
        self.assertEqual(repo.claim(self.identity, run_id, request, lease_seconds=90).state["result"], result)

    # spec: F12.ac7 — Two simultaneous claims yield exactly one active attempt.
    def test_owners_compete_for_single_claim(self):
        run_id = uuid4()
        ready = Barrier(2)

        def claim():
            ready.wait(timeout=5)
            try:
                return DraftRuns(DSN).claim(self.identity, run_id, {"fixture": True}, lease_seconds=90).stage
            except AgentError as exc:
                return exc.reason

        with ThreadPoolExecutor(max_workers=2) as workers:
            results = [workers.submit(claim) for _ in range(2)]
            self.assertCountEqual([item.result(timeout=10) for item in results], ["start", "draft_busy"])

    # spec: F12.ac7 — An unknown checkpoint format never becomes a fresh or cached success.
    def test_corrupt_checkpoint_fails_closed(self):
        run_id = uuid4()
        repo = DraftRuns(DSN)
        request = {"fixture": True}
        claim = repo.claim(self.identity, run_id, request, lease_seconds=90)
        repo.save(self.identity, claim, "gather", {"pins": {"fixture": "v1"}})
        repo.release(self.identity, claim)
        with tenant_session(DSN, role="app_rw", tenant_id=self.tenant) as conn:
            self.assertIsNone(conn.prepare_threshold)
            conn.execute("""UPDATE langgraph_checkpoints SET checkpoint = '{"version":2}' WHERE agent_id = %s""",
                         (run_id,))
        with self.assertRaisesRegex(AgentError, "invalid_draft_checkpoint"):
            DraftRuns(DSN).claim(self.identity, run_id, request, lease_seconds=90)

    # spec: F7.ac6 — Real F10 evaluation resolves a scoped employee and writes no audit rows.
    # spec: F12.ac4 — Identical employee and store ids can yield opposite verdicts across tenants.
    def test_scoped_compliance(self):
        from agents.labor_compliance.service import evaluate_proposal
        from compliance.engine import Proposal, load_scoped_snapshot

        ids = [uuid4(), uuid4()]
        with psycopg.connect(DSN) as conn:
            for tenant, wage_floor in zip(ids, (1, 24), strict=True):
                conn.execute("INSERT INTO tenants (id, slug, name, status) VALUES (%s,%s,'Synthetic','active')",
                             (tenant, f"test-{tenant}"))
                conn.execute("INSERT INTO stores (tenant_id, location_id) VALUES (%s,77)", (tenant,))
                conn.execute("INSERT INTO employer_config (tenant_id, employer_id) VALUES (%s,'fixture')", (tenant,))
                conn.execute("""INSERT INTO employees (tenant_id, employee_id, dob, role, hourly_rate, is_enrolled)
                           VALUES (%s,1,'1990-01-01','Barista',20,false)""", (tenant,))
                conn.execute("""INSERT INTO labor_rules (tenant_id, rule_id, jurisdiction, enforcement, metric,
                              operator, value_num, unit, priority, effective_from, verification_status)
                            VALUES (%s,'TEST-RATE','NY-STATE','HARD','rate','>=',%s,'USD',1,'2020-01-01','VERIFIED')
                            """, (tenant, wage_floor))
        try:
            request = {"version": 1, "employee_id": 1, "location_id": 77, "breaks": [],
                       "start": "2026-12-07T08:00:00-05:00", "end": "2026-12-07T10:00:00-05:00"}
            verdicts = [
                evaluate_proposal(DSN, ServiceIdentity(tenant, "fixture", "fixture"), request)
                for tenant in ids
            ]
            self.assertEqual([verdict["results"][0]["result"] for verdict in verdicts], ["PASS", "FAIL"])
            with tenant_session(DSN, role="app_ro", tenant_id=ids[0]) as conn:
                self.assertEqual(conn.execute("SELECT count(*) FROM rule_eval_log").fetchone()[0], 0)
                proposal = Proposal(str(ids[1]), 77, 1, "Barista", None, None, [])
                with self.assertRaisesRegex(Exception, "does not match"):
                    load_scoped_snapshot(conn, proposal)
        finally:
            with psycopg.connect(DSN) as conn:
                conn.execute("DELETE FROM tenants WHERE id = ANY(%s)", (ids,))

    @unittest.skipUnless(ADK, "CS011_ADK_PYTHON must point to the isolated ADK environment")
    # spec: F7.ac4 — Real interop through separately started Scheduler and ADK services.
    # spec: F12.ac1 — Real stage pipeline returns a solver-backed draft.
    # spec: F12.ac3 — Complete forward forecast is returned without persisting forecast rows.
    # spec: F12.ac9 — The production draft is replayable and leaves published schedules untouched.
    def test_full_wire_workflow(self):
        version = "cs015-fixture"
        run_id = uuid4()
        self._seed_week(version)
        with tenant_session(DSN, role="app_ro", tenant_id=self.tenant) as conn:
            before = self._domain_counts(conn)
        with tenant_session(DSN, role="app_ro", tenant_id=self.tenant) as conn:
            pins, _ = forecast_source(conn, version, (DAY, DAY))
        demand = from_json(json.dumps(score_forecast(
            DSN, version, self.identity, {"version": 1, "start": str(DAY), "end": str(DAY), **pins}
        )))
        with tenant_session(DSN, role="app_rw", tenant_id=self.tenant) as conn:
            first = demand.records[0]
            stale = replace(first, p70=Decimal(9999), upper=Decimal(9999))
            persist_forecasts(conn, replace(demand, records=(stale,)))
            inputs = assemble_solver_inputs(conn, target_window=(DAY, DAY), model_version=version, forecast=demand)
            self.assertEqual(inputs.demand.records[0].p70, first.p70)
            missing_category = replace(demand, records=tuple(
                record for record in demand.records if record.product_category != first.product_category
            ))
            with self.assertRaises(MissingInputError):
                assemble_solver_inputs(conn, target_window=(DAY, DAY), model_version=version, forecast=missing_category)
            before = (before[0] + 1, *before[1:])
        ingress = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        delegation = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            ingress_key = folder / "ingress.pem"
            forecast_key = folder / "forecast.pem"
            signing_key = folder / "delegation.pem"
            for file, key in ((ingress_key, ingress), (forecast_key, delegation)):
                file.write_bytes(key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo))
            signing_key.write_bytes(delegation.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
            signing_key.chmod(0o600)
            common = {"DATABASE_URL": DSN, "SCHEDULE_FORECAST_MODEL": version}
            with running_agent(ADK, "demand_forecast", {**common, "A2A_TRUSTED_ISSUER": "forecast-issuer",
                               "A2A_TRUST_PUBLIC_KEY_FILE": str(forecast_key)}) as forecast_url:
                settings = {**common, "A2A_FORECAST_ENDPOINT": forecast_url, "A2A_TRUSTED_ISSUER": "test-issuer",
                            "A2A_TRUST_PUBLIC_KEY_FILE": str(ingress_key), "A2A_FORECAST_ISSUER": "forecast-issuer",
                            "A2A_FORECAST_PRIVATE_KEY_FILE": str(signing_key),
                            "SCHEDULER_SOLVE_MAX_SECONDS": "30", "SCHEDULER_SOLVE_MAX_GAP": "0.9"}
                request = {"version": 1, "start": str(DAY), "end": "2026-12-13", "idempotency_key": str(run_id)}
                credential = self._credential(ingress)
                with running_agent(sys.executable, "scheduler", settings) as url:
                    response = httpx.post(url, json=message(request), timeout=85,
                                          headers={"Authorization": f"Bearer {credential}"})
                    self.assertEqual(response.status_code, 200)
                    body = response.json()
                    self.assertNotIn("error", body, body.get("error"))
                    result = body["result"]["parts"][0]["data"]
                    self.assertEqual(result["status"], "draft")
                    shifts = result["schedule"]["shifts"]
                    self.assertEqual(len({(shift["location_id"], shift["day"]) for shift in shifts}), 21)
                    self.assertTrue(all(Decimal(shift["end_hour"]) <= Decimal("12.5") for shift in shifts))
                    self.assertEqual(result["forecast"]["model_version"], version)
                    self.assertFalse(result["claims_legal_accuracy"])
                    headers = {"Authorization": f"Bearer {self._credential(ingress)}"}
                    invalid = httpx.post(url, json=message(request, protocol="9.0"), headers=headers)
                    self.assertEqual(invalid.json()["error"]["message"], "unsupported_protocol")
                with running_agent(sys.executable, "scheduler", settings) as url:
                    replay = asyncio.run(sdk_send(url, request, self._credential(ingress)))
                    self.assertEqual(replay, result)
        with tenant_session(DSN, role="app_rw", tenant_id=self.tenant) as conn:
            self.assertEqual(self._domain_counts(conn), before)

    @staticmethod
    def _domain_counts(conn):
        return conn.execute("""SELECT (SELECT count(*) FROM forecasts), (SELECT count(*) FROM schedules),
                    (SELECT count(*) FROM shifts), (SELECT count(*) FROM rule_eval_log)""").fetchone()

    def _credential(self, key):
        return token(key, "scheduler", "generate_schedule", tenant_id=str(self.tenant))

    def _seed_week(self, version):
        # A known-feasible three-store week. The baseline workbook has distinct staffing constraints.
        tenant, taxonomy = self.tenant, PINNED_TAXONOMY_VERSION
        with psycopg.connect(DSN) as conn:
            conn.execute("INSERT INTO taxonomy_meta (tenant_id,taxonomy_version) VALUES (%s,%s)", (tenant, taxonomy))
            conn.execute("""
                INSERT INTO product_taxonomy (tenant_id,product_id,product_category,taxonomy_version)
                VALUES (%s,1,'Coffee',%s),(%s,2,'Tea',%s)
                """, (tenant, taxonomy, tenant, taxonomy))
            conn.execute("""
                INSERT INTO throughput_standards (tenant_id,product_category,prep_role,seconds_per_unit,units_per_labor_hour)
                VALUES (%s,'Coffee','Barista',360,10),(%s,'Tea','Barista',360,10)
                """, (tenant, tenant))
            conn.execute("INSERT INTO employer_config (tenant_id,employer_id) VALUES (%s,'synthetic')", (tenant,))
            conn.execute("""
                INSERT INTO labor_rules (tenant_id,rule_id,jurisdiction,enforcement,metric,
                                         operator,value_num,unit,priority,effective_from,verification_status)
                VALUES (%s,'TEST-RATE','NY-STATE','HARD','rate','>=',15,'USD',1,'2020-01-01','VERIFIED')
                """, (tenant,))
            # Use the canonical rule vocabulary with synthetic stores, roster and demand.
            conn.execute("""
                INSERT INTO shift_rules (tenant_id,rule_id,description,value,unit,basis)
                SELECT %s,rule_id,description,value,unit,basis FROM shift_rules WHERE tenant_id = %s
                """, (tenant, TENANT))
            conn.execute("""
                INSERT INTO coverage_rules (tenant_id,rule_id,description,value,unit,basis,validity)
                SELECT %s,rule_id,description,value,unit,basis,validity FROM coverage_rules WHERE tenant_id = %s
                """, (tenant, TENANT))
            conn.execute("""
                INSERT INTO feature_sets (tenant_id,feature_version,source_window_start,source_window_end,taxonomy_version,
                                          row_count,has_basket_id,has_evening_history,has_annual_seasonality)
                VALUES (%s,%s,'2023-01-02','2023-01-03',%s,3,false,false,false)
                """, (tenant, version, taxonomy))
            for location in (71, 72, 73):
                conn.execute("""
                    INSERT INTO stores (tenant_id,location_id,store_location,max_staff_on_floor,stations)
                    VALUES (%s,%s,'Synthetic',4,4)
                    """, (tenant, location))
                conn.execute("""
                    INSERT INTO store_hours (tenant_id,location_id,day_of_week,open_time,close_time)
                    SELECT %s,%s,day,'08:00','12:30'
                    FROM unnest(ARRAY['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday']) day
                    """, (tenant, location))
                conn.execute("""
                    INSERT INTO cost_targets (tenant_id,location_id,target_labor_pct_of_sales,weekly_overtime_budget_usd,
                                              understaffing_penalty_weight,premium_penalty_weight)
                    VALUES (%s,%s,0.3,500,100,100)
                    """, (tenant, location))
                conn.execute("""
                    INSERT INTO employees (tenant_id,employee_id,dob,role,skill_level,home_location_id,
                        cross_location_eligible,is_key_holder,cash_authorised,food_protection_cert,hourly_rate,
                        availability_persona,min_hours_week,max_hours_week,is_enrolled,has_other_employer)
                    SELECT %s,%s * 10 + n,'1990-01-01','Barista',3,%s,false,true,true,true,20,'open',0,40,false,false
                    FROM generate_series(1,4) n
                    """, (tenant, location, location))
                conn.execute("""
                    INSERT INTO forecast_features (tenant_id,feature_version,location_id,product_category,obs_date,
                        obs_hour,items,revenue,day_of_week,is_weekend,is_holiday,school_in_session,low_history)
                    VALUES (%s,%s,%s,'Coffee','2023-01-02',8,10,50,0,false,false,false,false)
                    """, (tenant, version, location))
            conn.execute("""
                INSERT INTO forecast_models (tenant_id,model_version,feature_version,approach,taxonomy_version,params,
                    train_window_start,train_window_end,validation_window_start,validation_window_end,
                    validation_pinball,validation_coverage,candidates)
                VALUES (%s,%s,%s,'empirical_quantile',%s,
                    '{"approach":"empirical_quantile","payload":{"groups":[],"fallbacks":[],
                      "global":{"lower":9,"p70":10,"upper":11}}}',
                    '2023-01-02','2023-01-02','2023-01-03','2023-01-03',0,1,'{}')
                """, (tenant, version, version, taxonomy))


async def sdk_send(url, request, credential):
    wire = SendMessageRequest.model_validate(message(request))
    async with httpx.AsyncClient(timeout=10, headers={"Authorization": f"Bearer {credential}"}) as client:
        result = await JsonRpcTransport(client, url=url).send_message(wire.params)
        assert isinstance(result, Message)
        return result.parts[0].root.data


if __name__ == "__main__":
    unittest.main()
