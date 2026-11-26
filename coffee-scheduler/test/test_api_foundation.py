"""CS-030 gateway tests using the in-memory auth fixture and seeded store reads."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

SCHEDULER_ROOT = Path(__file__).resolve().parents[1]
if str(SCHEDULER_ROOT) not in sys.path:
    sys.path.insert(0, str(SCHEDULER_ROOT))

import jwt
import psycopg
from fastapi.testclient import TestClient
from psycopg_pool import ConnectionPool

from api.app import create_app
from api.config import GATEWAY_DATABASE_URL
from auth.service import TenantAuthService

LOGIN = {
    "tenant_slug": "maven-demo",
    "email": "owner@maven.example",
    "password": "correct horse battery staple",
}
FIXTURE_TENANT = "00000000-0000-0000-0000-000000000001"


class ApiFoundation(unittest.TestCase):
    def require_store_database(self):
        try:
            with psycopg.connect(GATEWAY_DATABASE_URL, connect_timeout=3):
                pass
        except psycopg.OperationalError as exc:
            self.skipTest(f"gateway database unavailable ({type(exc).__name__})")

    def _client(self):
        return self.enterContext(TestClient(create_app(TenantAuthService.for_contract_fixture())))

    def _login(self, client, body=None):
        return client.post("/auth/login", json=body or LOGIN)

    def _bearer(self, client):
        token = self._login(client).json()["access_token"]
        return {"authorization": f"Bearer {token}"}

    # spec: F30.ac — liveness needs no auth or DB.
    def test_healthz_ok(self):
        r = self._client().get("/healthz")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "ok")

    # spec: F30.ac / BUG-035 — valid credentials return a CS-061a token pair and set no session cookie.
    def test_login_success_returns_cs061a_tokens(self):
        c = self._client()
        r = self._login(c)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual({"access_token", "token_type", "expires_in", "refresh_token"}, set(r.json()))
        self.assertNotIn("session", c.cookies)

    # spec: F30.ac — wrong password fails closed (401), generic (no enumeration).
    def test_login_bad_password_401(self):
        r = self._login(self._client(), {**LOGIN, "password": "wrong-password"})
        self.assertEqual(r.status_code, 401)

    # spec: F30.ac — unknown tenant slug fails closed (401).
    def test_login_unknown_slug_401(self):
        r = self._login(self._client(), {**LOGIN, "tenant_slug": "no-such-tenant"})
        self.assertEqual(r.status_code, 401)

    # spec: F30.ac — /stores requires a credential (none → 401).
    def test_stores_requires_auth(self):
        self.assertEqual(self._client().get("/stores").status_code, 401)

    # spec: F30.ac — a tampered/garbage bearer is rejected (401), never trusted.
    def test_stores_tampered_token_401(self):
        r = self._client().get("/stores", headers={"authorization": "Bearer not-a-jwt"})
        self.assertEqual(r.status_code, 401)

    # spec: F30.ac — a foreign-signed token cannot spoof a tenant (signature/alg check → 401).
    def test_no_tenant_spoof_with_foreign_signature(self):
        forged = jwt.encode(
            {"tenant_id": "00000000-0000-0000-0000-000000000002", "user_id": "x",
             "sub": "x", "role": "owner", "is_saas_admin": False, "exp": 9999999999},
            "attacker-secret-not-ours",
            algorithm="HS256",
        )
        r = self._client().get("/stores", headers={"authorization": f"Bearer {forged}"})
        self.assertEqual(r.status_code, 401)

    # spec: F30.ac — an authenticated owner sees ONLY their tenant's stores (RLS-scoped from claims).
    def test_stores_returns_tenant_scoped_rows(self):
        self.require_store_database()
        c = self._client()
        r = c.get("/stores", headers=self._bearer(c))
        self.assertEqual(r.status_code, 200, r.text)
        stores = r.json()["stores"]
        self.assertEqual(len(stores), 3, "maven-demo owns exactly 3 stores")
        for s in stores:
            self.assertIn("location_id", s)
            self.assertIn("store_location", s)

    # spec: F30.ac2 — a real pool must survive other apps and be replaceable on restart.
    def test_store_reads_across_app_shutdown_and_restart(self):
        self.require_store_database()
        first = create_app(TenantAuthService.for_contract_fixture())
        second = create_app(TenantAuthService.for_contract_fixture())

        def assert_reads(client):
            response = client.get("/stores", headers=self._bearer(client))
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(len(response.json()["stores"]), 3)

        pools = []

        def record_pool(**kwargs):
            pool = ConnectionPool(**kwargs)
            pools.append(pool)
            return pool

        with patch("api.db.ConnectionPool", side_effect=record_pool):
            with TestClient(first) as client:
                assert_reads(client)
                with TestClient(second) as other:
                    assert_reads(other)
                self.assertEqual([False, True], [pool.closed for pool in pools])
                assert_reads(client)
            self.assertTrue(pools[0].closed)
            with TestClient(first) as restarted:
                assert_reads(restarted)
            self.assertEqual([True, True, True], [pool.closed for pool in pools])

    # spec: F30.ac — /me reflects the reconciled claims.
    def test_me_returns_claims(self):
        c = self._client()
        r = c.get("/me", headers=self._bearer(c))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["tenant_id"], FIXTURE_TENANT)
        self.assertEqual(r.json()["role"], "owner")


if __name__ == "__main__":
    unittest.main()
