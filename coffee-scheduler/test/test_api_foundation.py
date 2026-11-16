"""CS-030 — manager-UI API gateway foundation (F30), on the CS-061a auth boundary (BUG-035).

Exercises the auth → tenant-context → RLS → data path of the FastAPI gateway in-process via
Starlette's TestClient, AFTER the BUG-035 cutover: login issues a CS-061a RS256 access token, and
the tenant id is taken ONLY from the validated, reconciled token claims. A request with no /
tampered / foreign-signed credential is rejected 401 (fail-closed), and an authenticated request
sees only its own tenant's rows.

The auth service is the in-memory CS-061a contract fixture (maven-demo owner, tenant
00000000-0000-0000-0000-000000000001), so no AUTH_DATABASE_URL is needed; the RLS data read still
goes to the real database via api.db.fetch_stores. The legacy demo /auth/login + session cookie are
gone — the route-level auth boundary is covered offline in test_cs061a_gateway_integration.py.

Run: GATEWAY_DATABASE_URL=postgresql://coffeeshop:coffeeshop@localhost:6432/coffeeshop \
     python3 coffee-scheduler/test/test_api_foundation.py
Skips unless the gateway deps (incl. psycopg_pool) are importable AND the database is reachable.
"""
import os
import sys
import unittest
from pathlib import Path

SCHEDULER_ROOT = Path(__file__).resolve().parents[1]
if str(SCHEDULER_ROOT) not in sys.path:
    sys.path.insert(0, str(SCHEDULER_ROOT))

os.environ.setdefault(
    "GATEWAY_DATABASE_URL", "postgresql://coffeeshop:coffeeshop@localhost:6432/coffeeshop"
)

LOGIN = {
    "tenant_slug": "maven-demo",
    "email": "owner@maven.example",
    "password": "correct horse battery staple",
}
FIXTURE_TENANT = "00000000-0000-0000-0000-000000000001"

_SKIP = None
try:
    import jwt
    import psycopg
    import psycopg_pool  # noqa: F401 — gateway data pool dependency
    from fastapi.testclient import TestClient
    from api.app import create_app
    from auth.service import TenantAuthService
    # DB reachability probe (the gateway reads through PgBouncer).
    with psycopg.connect(os.environ["GATEWAY_DATABASE_URL"], connect_timeout=3) as _c:
        _c.execute("SELECT 1")
except Exception as exc:  # pragma: no cover - environment-dependent skip
    _SKIP = f"gateway deps or database unavailable: {exc}"


@unittest.skipIf(_SKIP, _SKIP or "")
class ApiFoundation(unittest.TestCase):
    def _client(self):
        # Fixture CS-061a auth (no AUTH DB) + real RLS-scoped store reads (default fetch_stores).
        return TestClient(create_app(TenantAuthService.for_contract_fixture()))

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
        c = self._client()
        r = c.get("/stores", headers=self._bearer(c))
        self.assertEqual(r.status_code, 200, r.text)
        stores = r.json()["stores"]
        self.assertEqual(len(stores), 3, "maven-demo owns exactly 3 stores")
        for s in stores:
            self.assertIn("location_id", s)
            self.assertIn("store_location", s)

    # spec: F30.ac — /me reflects the reconciled claims.
    def test_me_returns_claims(self):
        c = self._client()
        r = c.get("/me", headers=self._bearer(c))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["tenant_id"], FIXTURE_TENANT)
        self.assertEqual(r.json()["role"], "owner")


if __name__ == "__main__":
    unittest.main()
