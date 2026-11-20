"""CS-062 onboarding contracts: stdlib unittest, in-memory first and opt-in live DB."""
from __future__ import annotations

import os
import sys
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEDULER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCHEDULER_ROOT))


class OnboardingApiTests(unittest.TestCase):
    def setUp(self) -> None:
        from api.app import create_app
        from auth.service import TenantAuthService
        from fastapi.testclient import TestClient

        self.service = TenantAuthService.for_contract_fixture()
        self.client = TestClient(create_app(self.service, fetch_stores=lambda _: []))
        self.payload = {
            "slug": "new-roast", "name": "New Roast", "jurisdiction": "NY-STATE+NYC",
            "email": "owner@new-roast.example", "password": "correct horse battery staple",
        }

    def test_signup_is_trial_atomic_and_duplicate_is_conflict(self) -> None:
        response = self.client.post("/signup", json=self.payload)
        self.assertEqual(201, response.status_code, response.text)
        body = response.json()
        self.assertIn("verify_url", body)
        repo = self.service._repo
        tenant = repo._onboarding_tenants[body["tenant_id"]]
        self.assertEqual("trial", repo._tenant_status[body["tenant_id"]])
        self.assertIsNotNone(tenant["trial_ends_at"])
        self.assertIsNotNone(repo.get_tenant_user_by_id(body["tenant_id"], body["user_id"]))
        self.assertEqual(409, self.client.post("/signup", json=self.payload).status_code)

    def test_reserved_and_malformed_slug_reject_before_storage(self) -> None:
        for slug in ("ADMIN", "bad_slug", "-bad", "a" * 64):
            with self.subTest(slug=slug):
                response = self.client.post("/signup", json={**self.payload, "slug": slug})
                self.assertEqual(422, response.status_code)
        self.assertEqual({}, self.service._repo._onboarding_tenants)

    def test_verification_is_single_use_and_gates_owner_provisioning(self) -> None:
        signup = self.client.post("/signup", json=self.payload).json()
        access = {"authorization": f"Bearer {signup['access_token']}"}
        self.assertEqual(403, self.client.post("/onboarding/provision", headers=access).status_code)
        token = signup["verify_url"].split("=", 1)[1]
        self.assertEqual(200, self.client.post("/verify-email", json={"token": token}).status_code)
        self.assertEqual(422, self.client.post("/verify-email", json={"token": token}).status_code)
        first = self.client.post("/onboarding/provision", headers=access)
        second = self.client.post("/onboarding/provision", headers=access)
        self.assertEqual((200, {"provisioned": True}), (first.status_code, first.json()))
        self.assertEqual((200, {"provisioned": False}), (second.status_code, second.json()))

    def test_unknown_and_expired_verification_fail_uniformly(self) -> None:
        from api.onboarding import OnboardingService

        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        onboarding = OnboardingService(self.service, trial_days=14, verification_ttl_seconds=1, now=lambda: now)
        issued = onboarding.signup(**self.payload)
        token = issued.verify_url.split("=", 1)[1]
        now += timedelta(seconds=2)
        for candidate in (token, "unknown"):
            with self.subTest(candidate=candidate):
                with self.assertRaisesRegex(Exception, "verification failed"):
                    onboarding.verify_email(candidate)

    def test_unverified_jurisdiction_refuses_without_ny_default(self) -> None:
        signup = self.client.post("/signup", json={**self.payload, "slug": "other-state", "jurisdiction": "CA"}).json()
        token = signup["verify_url"].split("=", 1)[1]
        self.client.post("/verify-email", json={"token": token})
        response = self.client.post("/onboarding/provision", headers={"authorization": f"Bearer {signup['access_token']}"})
        self.assertEqual(422, response.status_code)
        self.assertIsNone(self.service._repo._onboarding_tenants[signup["tenant_id"]]["provisioned_at"])

    def test_repository_maps_unique_and_plpgsql_raise_to_typed_errors(self) -> None:
        from psycopg.errors import RaiseException, UniqueViolation
        from auth.repository import DatabaseAuthRepository, OnboardingConflictError, OnboardingProvisionError

        for error, typed in ((UniqueViolation("duplicate"), OnboardingConflictError),
                             (RaiseException("raised"), OnboardingConflictError)):
            with self.subTest(error=error.__class__.__name__), self.assertRaises(typed):
                DatabaseAuthRepository._onboarding_exception(error, typed)
        with self.assertRaises(OnboardingProvisionError):
            DatabaseAuthRepository._onboarding_exception(RaiseException("unverified"), OnboardingProvisionError)


class OnboardingLiveDatabaseTests(unittest.TestCase):
    """Rollback-only proof when a local direct PostgreSQL URL is explicitly supplied."""

    def setUp(self) -> None:
        self.dsn = os.environ.get("DATABASE_URL")
        if not self.dsn:
            self.skipTest("DATABASE_URL not set for CS-062 live DB checks")

    def test_provision_templates_and_trial_suspension_are_idempotent(self) -> None:
        import psycopg

        slug = f"cs062-{uuid.uuid4().hex[:16]}"
        now = datetime.now(timezone.utc)
        with psycopg.connect(self.dsn, prepare_threshold=None) as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM onboarding_signup(%s,%s,%s,%s,%s,%s,%s,%s)",
                                (slug, "CS 062", "NY-STATE+NYC", "America/New_York", "owner@example.test", "$argon2id$fixture", 14, now))
                    tenant_id, user_id = cur.fetchone()
                    cur.execute("SELECT status,trial_ends_at FROM tenants WHERE id=%s", (tenant_id,))
                    self.assertEqual("trial", cur.fetchone()[0])
                    cur.execute("SELECT onboarding_provision(%s,%s,%s)", (tenant_id, "NY-STATE+NYC", now))
                    self.assertTrue(cur.fetchone()[0])
                    cur.execute("SELECT count(*) FROM labor_rules WHERE tenant_id=%s", (tenant_id,)); rules = cur.fetchone()[0]
                    cur.execute("SELECT count(*) FROM throughput_standards WHERE tenant_id=%s", (tenant_id,)); standards = cur.fetchone()[0]
                    self.assertGreater(rules, 0); self.assertGreater(standards, 0)
                    cur.execute("SELECT onboarding_provision(%s,%s,%s)", (tenant_id, "NY-STATE+NYC", now))
                    self.assertFalse(cur.fetchone()[0])
                    cur.execute("SELECT count(*) FROM labor_rules WHERE tenant_id=%s", (tenant_id,)); self.assertEqual(rules, cur.fetchone()[0])
                    cur.execute("SELECT count(*) FROM throughput_standards WHERE tenant_id=%s", (tenant_id,)); self.assertEqual(standards, cur.fetchone()[0])
                    cur.execute("SELECT onboarding_suspend_expired_trials(%s)", (now + timedelta(days=15),)); self.assertEqual(1, cur.fetchone()[0])
                    cur.execute("SELECT onboarding_suspend_expired_trials(%s)", (now + timedelta(days=15),)); self.assertEqual(0, cur.fetchone()[0])
                    cur.execute("SELECT * FROM onboarding_signup(%s,%s,%s,%s,%s,%s,%s,%s)",
                                (f"{slug}-ca", "CS 062 CA", "CA", "America/New_York", "ca@example.test", "$argon2id$fixture", 14, now))
                    ca_tenant_id, _ = cur.fetchone()
                    with self.assertRaises(psycopg.errors.RaiseException):
                        with conn.transaction():
                            cur.execute("SELECT onboarding_provision(%s,%s,%s)", (ca_tenant_id, "CA", now))
                    cur.execute("SELECT count(*) FROM labor_rules WHERE tenant_id=%s", (ca_tenant_id,))
                    self.assertEqual(0, cur.fetchone()[0], "unverified jurisdiction must never receive NY rules")
            finally:
                conn.rollback()


if __name__ == "__main__":
    unittest.main()
