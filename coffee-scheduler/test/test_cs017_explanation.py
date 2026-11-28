"""CS-017 grounding, API, and rollback-only PostgreSQL checks."""
import json
import os
import sys
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

SCHEDULER_ROOT = Path(__file__).resolve().parents[1]
if str(SCHEDULER_ROOT) not in sys.path:
    sys.path.insert(0, str(SCHEDULER_ROOT))

import psycopg
from fastapi.testclient import TestClient
from psycopg.conninfo import conninfo_to_dict
from psycopg.transaction import Rollback
from test_api_foundation import FIXTURE_TENANT, LOGIN

from agents.explanation.grounding import RuleFact, ShiftEvidence, ShiftNotFound, gather_evidence
from agents.explanation.model import AnthropicExplainer
from agents.explanation.service import explain_assignment
from api.app import create_app
from api.db import GatewayDatabase
from auth.service import TenantAuthService
from compliance import engine
from contracts.forecast import load_vocabulary
from data_access.session import open_session_context

FACT = RuleFact("NY-REST", "PREMIUM", "The rest gap was 8 hours.", Decimal("12.50"))
OTHER_FACT = RuleFact("NY-WEEKLY", "PASS", "Weekly hours were within the limit.", None)
EVIDENCE = ShiftEvidence(frozenset({FACT, OTHER_FACT}), frozenset({"Coffee"}))
CANNED = "[NY-REST] PREMIUM — The rest gap was 8 hours. Premium: $12.50."


class ExplanationService(unittest.TestCase):
    def setUp(self):
        self.conn = Mock()
        self.shift_id = uuid4()
        self.read = self.enterContext(patch("agents.explanation.service.gather_evidence", return_value=EVIDENCE))
        self.sdk = self.enterContext(patch("anthropic.Anthropic", side_effect=AssertionError("real API forbidden")))

    def explain(self, client):
        return explain_assignment(self.conn, FIXTURE_TENANT, self.shift_id, model_client=client)

    # spec: F13.ac1
    # spec: F13.ac2
    # spec: F13.ac6
    def test_accepts_evidence_prose(self):
        client = Mock(return_value=CANNED)
        result = self.explain(client)
        self.assertTrue(result.grounded)
        self.assertIn(FACT.rule_id, result.cited_rule_ids)
        self.assertLessEqual(set(result.cited_rule_ids), {fact.rule_id for fact in EVIDENCE.facts})
        self.assertIn("8 hours", result.text)
        self.assertIn("$12.50", result.text)
        self.read.assert_called_once_with(self.conn, FIXTURE_TENANT, self.shift_id)
        system, user = client.call_args.args
        self.assertIn("ONLY", system)
        self.assertEqual(json.loads(user)["categories"], ["Coffee"])
        self.assertNotIn(FIXTURE_TENANT, user)
        self.assertNotIn(str(self.shift_id), user)

    # spec: F13.ac1
    # spec: F13.ac2
    # spec: F13.ac5
    def test_rejects_fabricated_ids_numbers_verdicts_and_legal_claims(self):
        for malicious in (
            CANNED + "\n[BOGUS-RULE] PASS — The limit was 999 hours.",
            CANNED.replace("8 hours", "999 hours"),
            CANNED.replace("8 hours", "nine hours"),
            CANNED.replace("8 hours", "12.50 hours"),
            CANNED.replace("PREMIUM", "PASS"),
            CANNED + " The shift is legally compliant.",
            CANNED.replace("The rest gap was", "The required rest gap was"),
        ):
            with self.subTest(malicious=malicious):
                result = self.explain(Mock(return_value=malicious))
                self.assertFalse(result.grounded)
                self.assertFalse(result.claims_legal_accuracy)
                self.assertIn("unconfirmed", result.text.lower())
                self.assertLessEqual(set(result.cited_rule_ids), {fact.rule_id for fact in EVIDENCE.facts})
                self.assertNotIn("BOGUS-RULE", result.text)
                self.assertNotIn("999", result.text)
                self.assertNotIn("legally compliant", result.text)
                self.assertIn(FACT.reason, result.text)

    # spec: F13.ac4
    def test_no_evidence_skips_model(self):
        self.read.return_value = ShiftEvidence(frozenset(), frozenset())
        client = Mock()
        result = self.explain(client)
        client.assert_not_called()
        self.assertFalse(result.grounded)
        self.assertFalse(result.cited_rule_ids)
        self.assertIn("insufficient evidence", result.text.lower())

    # spec: F13.ac6
    def test_unset_key_returns_deterministic_evidence(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
            os.environ.pop("ANTHROPIC_API_KEY")
            result = self.explain(AnthropicExplainer())
            self.assertEqual(result, self.explain(AnthropicExplainer()))
        self.sdk.assert_not_called()
        self.assertFalse(result.grounded)
        self.assertIn(FACT.reason, result.text)

    # spec: F13.ac6
    def test_sdk_uses_configured_model_and_reads_only_text_blocks(self):
        self.sdk.side_effect = None
        create = self.sdk.return_value.__enter__.return_value.messages.create
        create.return_value = SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="thinking"), SimpleNamespace(type="text", text=CANNED)],
        )
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "synthetic-test-key"}):
            self.assertTrue(self.explain(AnthropicExplainer()).grounded)
        self.assertEqual(create.call_args.kwargs["model"], "claude-sonnet-5-5")
        self.assertEqual(create.call_args.kwargs["thinking"], {"type": "adaptive"})
        self.assertEqual(create.call_args.kwargs["output_config"], {"effort": "low"})
        self.assertEqual(create.call_args.kwargs["max_tokens"], 1024)


class ExplanationApi(unittest.TestCase):
    # spec: F13.ac3
    def test_route_authenticates_and_uses_only_the_principal_tenant(self):
        shift_id = uuid4()
        path = f"/schedule/shift/{shift_id}/explain"
        app = create_app(TenantAuthService.for_contract_fixture())
        with (TestClient(app) as client, patch.object(GatewayDatabase, "_pool_for_request") as pool,
              patch("api.onboarding.OnboardingService.is_email_verified", return_value=True),
              patch("agents.explanation.service.gather_evidence", return_value=EVIDENCE) as gather,
              patch("anthropic.Anthropic", side_effect=AssertionError("real API forbidden")),
              patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""})):
            self.assertEqual(client.get(path).status_code, 401)
            pool.assert_not_called()
            login = client.post("/auth/login", json=LOGIN)
            self.assertEqual(login.status_code, 200)
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
            with patch.object(TenantAuthService, "authorize_access_request") as authorize:
                authorize.return_value.role = "employee"
                self.assertEqual(client.get(path, headers=headers).status_code, 403)
                pool.assert_not_called()
            response = client.get(f"{path}?tenant_id={uuid4()}", headers=headers)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertFalse(response.json()["claims_legal_accuracy"])
            gather.assert_called_once_with(
                pool.return_value.connection.return_value.__enter__.return_value, FIXTURE_TENANT, shift_id,
            )
            gather.side_effect = ShiftNotFound
            self.assertEqual(client.get(path, headers=headers).status_code, 404)


class EvidenceDatabase(unittest.TestCase):
    def require_local_database(self):
        dsn = os.environ.get("DATABASE_URL")
        if not dsn:
            self.skipTest("set DATABASE_URL to the local disposable coffeeshop database")
        config = conninfo_to_dict(dsn)
        if (config.get("host") not in {"localhost", "127.0.0.1", "::1"}
                or config.get("dbname") != "coffeeshop" or config.get("hostaddr") or config.get("service")):
            self.skipTest("rollback fixture requires the local coffeeshop database")
        try:
            return psycopg.connect(dsn, connect_timeout=3, prepare_threshold=None)
        except psycopg.OperationalError as exc:
            self.skipTest(f"local PostgreSQL unavailable ({type(exc).__name__})")

    def insert_shift(self, conn, tenant, *, with_log):
        location = 193017
        schedule_id = uuid4()
        shift_id = uuid4()
        conn.execute("""
            INSERT INTO schedules (tenant_id, schedule_id, location_id, week_start, status)
            VALUES (%s,%s,%s,'2026-03-02','published')
            """, (tenant, schedule_id, location))
        conn.execute("""
            INSERT INTO shifts (tenant_id, shift_id, schedule_id, location_id, start_at, end_at, role)
            VALUES (%s,%s,%s,%s,'2026-03-03T14:00Z','2026-03-03T18:00Z','Barista')
            """, (tenant, shift_id, schedule_id, location))
        conn.execute("""
            INSERT INTO shift_assignments (tenant_id, shift_id, employee_id)
            VALUES (%s,%s,%s)
            """, (tenant, shift_id, location))
        if with_log:
            conn.execute("""
                INSERT INTO rule_eval_log
                    (tenant_id, schedule_id, shift_id, employee_id, rule_id, result, notes)
                VALUES (%s,%s,%s,%s,'NY-FIXTURE','PASS','Recorded fixture reason')
                """, (tenant, schedule_id, shift_id, location))
        return shift_id

    # spec: F13.ac3
    # spec: F13.ac4
    # spec: F13.ac7
    def test_rollback_reads_only_scoped_evidence_and_judges_without_a_log(self):
        foreign = uuid4()
        with self.require_local_database() as conn:
            with conn.transaction():
                original_context = conn.execute(
                    "SELECT current_user, current_setting('app.tenant_id', true)"
                ).fetchone()
                conn.execute("""
                    INSERT INTO tenants (id, slug, name, status) VALUES (%s,%s,'CS-017 fixture','active')
                    """, (foreign, f"fixture-{foreign}"))
                for tenant in (FIXTURE_TENANT, foreign):
                    conn.execute("INSERT INTO stores (tenant_id, location_id) VALUES (%s,193017)", (tenant,))
                    conn.execute("""
                        INSERT INTO employees
                            (tenant_id, employee_id, role, dob, hourly_rate, is_enrolled, food_protection_cert)
                        VALUES (%s,193017,'Barista','1990-01-01',25,false,true)
                        """, (tenant,))
                logged = self.insert_shift(conn, FIXTURE_TENANT, with_log=True)
                pending = self.insert_shift(conn, FIXTURE_TENANT, with_log=False)
                hidden = self.insert_shift(conn, foreign, with_log=True)
                with conn.transaction(force_rollback=True):
                    open_session_context(conn, role="app_ro", tenant_id=FIXTURE_TENANT)
                    categories = load_vocabulary(conn)
                with patch.object(engine, "judge", wraps=engine.judge) as judge:
                    evidence = gather_evidence(conn, FIXTURE_TENANT, logged)
                    self.assertEqual(
                        evidence.facts, frozenset({RuleFact("NY-FIXTURE", "PASS", "Recorded fixture reason", None)})
                    )
                    self.assertEqual(evidence.categories, categories)
                    for unavailable in (hidden, uuid4()):
                        with self.assertRaises(ShiftNotFound):
                            gather_evidence(conn, FIXTURE_TENANT, unavailable)
                    judge.assert_not_called()
                    live = gather_evidence(conn, FIXTURE_TENANT, pending)
                    judge.assert_called_once()
                    snapshot, proposal = judge.call_args.args
                    self.assertEqual(snapshot.tenant_id, FIXTURE_TENANT)
                    self.assertEqual(proposal.shift_id, str(pending))
                    self.assertTrue(live.facts)
                    self.assertEqual(live.categories, categories)
                    self.assertNotIn("NY-FIXTURE", {fact.rule_id for fact in live.facts})
                conn.execute(
                    "UPDATE employees SET hourly_rate = NULL WHERE tenant_id = %s AND employee_id = 193017",
                    (FIXTURE_TENANT,),
                )
                self.assertFalse(gather_evidence(conn, FIXTURE_TENANT, pending).facts)
                restored_context = conn.execute(
                    "SELECT current_user, current_setting('app.tenant_id', true)"
                ).fetchone()
                self.assertEqual((restored_context[0], restored_context[1] or ""),
                                 (original_context[0], original_context[1] or ""))
                self.assertIsNone(conn.execute("SELECT 1 FROM rule_eval_log WHERE shift_id = %s", (pending,)).fetchone())
                raise Rollback()
            self.assertIsNone(conn.execute("SELECT 1 FROM shifts WHERE shift_id = %s", (logged,)).fetchone())


if __name__ == "__main__":
    unittest.main()
