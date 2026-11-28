"""CS-031 route and database checks using a rolled-back fixture."""
import contextlib
import sys
import unittest
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

SCHEDULER_ROOT = Path(__file__).resolve().parents[1]
if str(SCHEDULER_ROOT) not in sys.path:
    sys.path.insert(0, str(SCHEDULER_ROOT))

import jwt
import psycopg
from fastapi.testclient import TestClient
from psycopg.transaction import Rollback
from test_api_foundation import FIXTURE_TENANT, LOGIN

from agents.scheduler.persist import persist_schedule
from agents.scheduler.runner import ScheduledShift, ScheduleEconomics, ScheduleOutput
from agents.scheduler.solve import SolveResult, SolverStopPolicy
from api.app import create_app
from api.config import GATEWAY_DATABASE_URL
from api.db import GatewayDatabase
from api.routing import TenantRoutingConfig
from auth.service import TenantAuthService

WEEK = "2040-01-02"
LOCATION = 193101
SCHEDULE = f"/schedule?location_id={LOCATION}&week_start={WEEK}"


class ScheduleApi(unittest.TestCase):
    def require_store_database(self):
        try:
            return psycopg.connect(GATEWAY_DATABASE_URL, connect_timeout=3, prepare_threshold=None)
        except psycopg.OperationalError as exc:
            self.skipTest(f"gateway database unavailable ({type(exc).__name__})")

    def client(self):
        return self.enterContext(TestClient(create_app(TenantAuthService.for_contract_fixture())))

    def bearer(self, client):
        response = client.post("/auth/login", json=LOGIN)
        self.assertEqual(response.status_code, 200, response.text)
        return {"authorization": f"Bearer {response.json()['access_token']}"}

    # spec: F31.ac1
    def test_missing_or_forged_credentials_are_rejected(self):
        client = self.client()
        forged = jwt.encode({"tenant_id": FIXTURE_TENANT, "role": "owner"}, "forged-secret-key-long-enough-123456")
        for path in (SCHEDULE, f"/schedule/shift/{uuid4()}/rules"):
            self.assertEqual(client.get(path).status_code, 401)
            for token in ("bad-token", forged):
                self.assertEqual(client.get(path, headers={"authorization": f"Bearer {token}"}).status_code, 401)

    # spec: F31.ac1
    def test_requires_verified_email_and_manager_role(self):
        client = self.client()
        headers = self.bearer(client)
        with patch("api.onboarding.OnboardingService.is_email_verified", return_value=False):
            self.assertEqual(client.get(SCHEDULE, headers=headers).status_code, 403)
        with (patch("api.app.TenantAuthService.authorize_access_request") as authorize,
              patch("api.onboarding.OnboardingService.is_email_verified", return_value=True)):
            authorize.return_value.role = "employee"
            self.assertEqual(client.get(SCHEDULE, headers=headers).status_code, 403)

    # spec: F31.ac1
    def test_signed_principal_must_match_tenant_route(self):
        foreign = str(uuid4())
        config = TenantRoutingConfig(mode="development", platform_suffix="example.test", https_enabled=False)
        app = create_app(
            TenantAuthService.for_contract_fixture(),
            routing_config=config,
            tenant_lookup={"maven-demo": FIXTURE_TENANT, "foreign": foreign}.get,
        )
        with TestClient(app, base_url="http://localhost") as client:
            login = client.post("/t/maven-demo/auth/login", json=LOGIN)
            self.assertEqual(login.status_code, 200, login.text)
            headers = {"authorization": f"Bearer {login.json()['access_token']}"}
            with patch.object(GatewayDatabase, "_pool_for_request") as pool:
                self.assertEqual(client.get(f"/t/foreign{SCHEDULE}", headers=headers).status_code, 403)
                pool.assert_not_called()

    # spec: F31.ac7
    def test_schedule_routes_are_get_only(self):
        client = self.client()
        headers = self.bearer(client)
        for path in (SCHEDULE, f"/schedule/shift/{uuid4()}/rules"):
            for verb in ("POST", "PATCH", "PUT", "DELETE"):
                self.assertEqual(client.request(verb, path, headers=headers).status_code, 405)

    def insert_schedule(self, conn, tenant, status, *, location=LOCATION, deleted=False):
        schedule_id = conn.execute("""
            INSERT INTO schedules (tenant_id, location_id, week_start, status, deleted_at)
            VALUES (%s,%s,%s,%s,CASE WHEN %s THEN now() END) RETURNING schedule_id
            """, (tenant, location, WEEK, status, deleted)).fetchone()[0]
        shift_id = conn.execute("""
            INSERT INTO shifts (tenant_id, schedule_id, location_id, station_id, start_at, end_at, role)
            VALUES (%s,%s,%s,'F31-bar','2040-01-03T00:30Z','2040-01-03T03:30Z','Barista')
            RETURNING shift_id
            """, (tenant, schedule_id, location)).fetchone()[0]
        conn.execute("""
            INSERT INTO shift_assignments (tenant_id, shift_id, employee_id, is_meal_break_covered)
            VALUES (%s,%s,%s,true)
            """, (tenant, shift_id, LOCATION))
        conn.execute("""
            INSERT INTO rule_eval_log (tenant_id, schedule_id, shift_id, employee_id, rule_id, result, notes)
            VALUES (%s,%s,%s,%s,'F31-rule','PASS','Recorded compliance reason')
            """, (tenant, schedule_id, shift_id, LOCATION))
        return str(schedule_id), str(shift_id)

    # spec: F31.ac1
    # spec: F31.ac3
    # spec: F31.ac4
    # spec: F31.ac5
    # spec: F31.ac6
    def test_published_roster_and_rules_are_scoped_by_rls(self):
        client = self.client()
        headers = self.bearer(client)
        foreign = uuid4()
        with self.require_store_database() as conn, conn.transaction():
            conn.execute("INSERT INTO tenants (id, slug, name, status) VALUES (%s,'f31-test','F31 fixture','active')", (foreign,))
            for tenant, locations in ((FIXTURE_TENANT, (LOCATION,)), (foreign, (LOCATION, LOCATION + 1))):
                for location in locations:
                    conn.execute("INSERT INTO stores (tenant_id, location_id) VALUES (%s,%s)", (tenant, location))
                conn.execute("""
                    INSERT INTO employees (tenant_id, employee_id, first_name, last_name)
                    VALUES (%s,%s,'Demo','Employee')
                    """, (tenant, LOCATION))
                conn.execute("""
                    INSERT INTO stations (tenant_id, location_id, station_id, station_type, staffed_by_role)
                    VALUES (%s,%s,'F31-bar','Espresso','Barista'), (%s,%s,'F31-till','Till','Register')
                    """, (tenant, LOCATION, tenant, LOCATION))
            with self.assertRaises(psycopg.errors.CheckViolation), conn.transaction():
                self.insert_schedule(conn, FIXTURE_TENANT, "invalid")
            live_id, live_shift = self.insert_schedule(conn, FIXTURE_TENANT, "published")
            _, draft_shift = self.insert_schedule(conn, FIXTURE_TENANT, "draft")
            _, superseded_shift = self.insert_schedule(conn, FIXTURE_TENANT, "superseded")
            _, deleted_shift = self.insert_schedule(conn, FIXTURE_TENANT, "published", deleted=True)
            _, foreign_shift = self.insert_schedule(conn, foreign, "published")
            self.insert_schedule(conn, foreign, "published", location=LOCATION + 1)

            # The real database methods borrow the fixture transaction, whose rows a second
            # connection cannot see. Roll back the savepoint after each borrow to reset SET LOCAL.
            @contextlib.contextmanager
            def borrow():
                with conn.transaction():
                    yield conn
                    raise Rollback()

            with (patch.object(GatewayDatabase, "_pool_for_request") as pool,
                  patch("api.onboarding.OnboardingService.is_email_verified", return_value=True)):
                pool.return_value.connection = borrow
                response = client.get(f"{SCHEDULE}&tenant_id={foreign}", headers=headers)
                self.assertEqual(response.status_code, 200, response.text)
                roster = response.json()
                self.assertEqual(roster["schedule_id"], live_id)
                self.assertEqual(roster["status"], "published")
                self.assertEqual(roster["timezone"], "America/New_York")
                self.assertEqual([s["shift_id"] for s in roster["shifts"]], [live_shift])
                self.assertEqual(roster["shifts"][0]["employee_name"], "Demo Employee")
                self.assertTrue(roster["shifts"][0]["is_meal_break_covered"])
                self.assertEqual([s["station_id"] for s in roster["stations"]], ["F31-bar", "F31-till"])

                for query in (
                    f"location_id={LOCATION}&week_start=2040-01-09",
                    f"location_id={LOCATION + 1}&week_start={WEEK}",
                ):
                    empty = client.get(f"/schedule?{query}", headers=headers)
                    self.assertEqual(empty.status_code, 200, empty.text)
                    self.assertIsNone(empty.json()["schedule_id"])
                    self.assertEqual(empty.json()["shifts"], [])
                self.assertEqual(client.get(SCHEDULE.replace(str(LOCATION), "wrong"), headers=headers).status_code, 422)

                rules = client.get(f"/schedule/shift/{live_shift}/rules", headers=headers)
                self.assertEqual(rules.status_code, 200, rules.text)
                self.assertEqual([(r["rule_id"], r["result"], r["reason"]) for r in rules.json()["rules"]],
                                 [("F31-rule", "PASS", "Recorded compliance reason")])
                for shift in (draft_shift, superseded_shift, deleted_shift, foreign_shift, uuid4()):
                    hidden = client.get(f"/schedule/shift/{shift}/rules", headers=headers)
                    self.assertEqual(hidden.status_code, 200, hidden.text)
                    self.assertEqual(hidden.json(), {"rules": []})
            raise Rollback()


    # spec: F31.ac2
    # spec: F31.ac5
    # spec: F31.ac6
    def test_persist_converts_dst_and_writes_rule_evidence_atomically(self):
        week = date(2026, 3, 2)
        with self.require_store_database() as conn, conn.transaction():
            conn.execute("INSERT INTO stores (tenant_id, location_id) VALUES (%s,%s)", (FIXTURE_TENANT, LOCATION))
            conn.execute("""
                INSERT INTO employees
                    (tenant_id, employee_id, role, dob, hourly_rate, is_enrolled, food_protection_cert)
                VALUES (%s,%s,'Barista','1990-01-01',25,false,true)
                """, (FIXTURE_TENANT, LOCATION))
            shift = ScheduledShift(
                employee_id=LOCATION, role="Barista", location_id=LOCATION, day=date(2026, 3, 8),
                start_hour=Decimal("1.5"), end_hour=Decimal("3.5"), hours=Decimal(1),
                hourly_rate=Decimal(25), labour_cost_cents=2500, premium_cents=0, premium_rule_ids=(),
                station_id="F31-bar", is_meal_break_covered=False,
            )
            output = ScheduleOutput(
                target_window=(week, shift.day), model_version="test-fixture", shifts=(shift,),
                economics=ScheduleEconomics(2500, 0, 2, 1, {LOCATION: 2500}),
                solve=SolveResult((), 0, "NOT_RUN", 0, 0, "limit_or_unknown", 0, SolverStopPolicy(1, 0)),
                objective=0, omitted_terms=(),
            )
            args = {"tenant_id": FIXTURE_TENANT, "location_id": LOCATION, "week_start": week,
                    "solver_version": "test-fixture"}
            old_id = persist_schedule(conn, output, **args)

            with (patch("compliance.engine.evaluate", side_effect=RuntimeError("evaluation failed")),
                  self.assertRaisesRegex(RuntimeError, "evaluation failed")):
                persist_schedule(conn, output, **args)
            self.assertEqual(conn.execute("""
                SELECT schedule_id, status FROM schedules WHERE location_id = %s
                """, (LOCATION,)).fetchall(), [(old_id, "published")])

            live_id = persist_schedule(conn, output, **args)
            self.assertEqual(conn.execute("""
                SELECT status, deleted_at IS NOT NULL FROM schedules WHERE schedule_id = %s
                """, (old_id,)).fetchone(), ("superseded", True))
            row = conn.execute("""
                SELECT shift.start_at, shift.end_at, shift.station_id, assignment.is_meal_break_covered,
                       (SELECT count(*) FROM rule_eval_log AS log
                        WHERE log.tenant_id = shift.tenant_id AND log.shift_id = shift.shift_id
                          AND log.schedule_id = shift.schedule_id AND log.employee_id = assignment.employee_id)
                FROM shifts AS shift JOIN shift_assignments AS assignment USING (tenant_id, shift_id)
                WHERE shift.schedule_id = %s
                """, (live_id,)).fetchone()
            self.assertEqual(row[:4], (
                datetime(2026, 3, 8, 6, 30, tzinfo=UTC), datetime(2026, 3, 8, 7, 30, tzinfo=UTC),
                "F31-bar", False,
            ))
            self.assertGreater(row[4], 0)
            raise Rollback()


if __name__ == "__main__":
    unittest.main()
