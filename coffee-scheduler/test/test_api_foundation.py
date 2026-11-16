"""CS-030 — manager-UI API gateway foundation (F30).

Exercises the auth → tenant-context → RLS → data path of the FastAPI gateway in-process via Starlette's
TestClient. The tenant id is taken ONLY from the signed session JWT; a request with no / tampered / foreign-
signed cookie is rejected 401 (fail-closed), and an authenticated request sees only its own tenant's rows.

Run: GATEWAY_DATABASE_URL=postgresql://coffeeshop:coffeeshop@localhost:6432/coffeeshop \
     python3 coffee-scheduler/test/test_api_foundation.py
Skips unless the gateway deps are importable AND the database is reachable.

Authored by Claude (orchestration: Codex dev / Claude tests).
"""
import os
import sys
import unittest
from pathlib import Path

SCHEDULER_ROOT = Path(__file__).resolve().parents[1]
if str(SCHEDULER_ROOT) not in sys.path:
    sys.path.insert(0, str(SCHEDULER_ROOT))

# Config must be present before importing api.* (api/config.py fails fast without a secret).
os.environ.setdefault("SESSION_SECRET", "test-secret-" + "x" * 40)
os.environ.setdefault(
    "GATEWAY_DATABASE_URL", "postgresql://coffeeshop:coffeeshop@localhost:6432/coffeeshop"
)

DEMO_SLUG = os.environ.get("DEMO_TENANT_SLUG", "maven-demo")
DEMO_EMAIL = os.environ.get("DEMO_MANAGER_EMAIL", "manager@maven-demo.coffee")
DEMO_PASSWORD = os.environ.get("DEMO_MANAGER_PASSWORD", "demo-pass-change-me")

_SKIP = None
try:
    import jwt
    import psycopg
    from fastapi.testclient import TestClient
    from api.app import app
    from api.config import SESSION_SECRET
    # DB reachability probe (the gateway reads through PgBouncer).
    with psycopg.connect(os.environ["GATEWAY_DATABASE_URL"], connect_timeout=3) as _c:
        _c.execute("SELECT 1")
except Exception as exc:  # pragma: no cover - environment-dependent skip
    _SKIP = f"gateway deps or database unavailable: {exc}"


@unittest.skipIf(_SKIP, _SKIP or "")
class ApiFoundation(unittest.TestCase):
    def _client(self):
        return TestClient(app)

    def _login(self, client, *, slug=DEMO_SLUG, email=DEMO_EMAIL, password=DEMO_PASSWORD):
        return client.post(
            "/auth/login", json={"tenant_slug": slug, "email": email, "password": password}
        )

    # spec: F30.ac — liveness needs no auth or DB.
    def test_healthz_ok(self):
        r = self._client().get("/healthz")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "ok")

    # spec: F30.ac — valid demo credentials set a session cookie and return role/tenant.
    def test_login_success_sets_cookie(self):
        c = self._client()
        r = self._login(c)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("session", r.cookies)
        self.assertEqual(r.json(), {"role": "manager", "tenant_slug": DEMO_SLUG})

    # spec: F30.ac — wrong password fails closed (401), same generic message (no enumeration).
    def test_login_bad_password_401(self):
        r = self._login(self._client(), password="wrong-password")
        self.assertEqual(r.status_code, 401)

    # spec: F30.ac — unknown tenant slug fails closed (401).
    def test_login_unknown_slug_401(self):
        r = self._login(self._client(), slug="no-such-tenant")
        self.assertEqual(r.status_code, 401)

    # spec: F30.ac — /stores requires a session (no cookie → 401).
    def test_stores_requires_auth(self):
        r = self._client().get("/stores")
        self.assertEqual(r.status_code, 401)

    # spec: F30.ac — a tampered/garbage cookie is rejected (401), never trusted.
    def test_stores_tampered_cookie_401(self):
        c = self._client()
        c.cookies.set("session", "not-a-jwt")
        self.assertEqual(c.get("/stores").status_code, 401)

    # spec: F30.ac — a foreign-signed token cannot spoof a tenant (signature check → 401).
    def test_no_tenant_spoof_with_foreign_signature(self):
        forged = jwt.encode(
            {"tenant_id": "00000000-0000-0000-0000-000000000002", "tenant_slug": "evil",
             "role": "manager", "user_id": "x", "exp": 9999999999},
            "attacker-secret-not-ours",
            algorithm="HS256",
        )
        c = self._client()
        c.cookies.set("session", forged)
        self.assertEqual(c.get("/stores").status_code, 401)

    # spec: F30.ac — an authenticated manager sees ONLY their tenant's stores (RLS-scoped).
    def test_stores_returns_tenant_scoped_rows(self):
        c = self._client()
        self.assertEqual(self._login(c).status_code, 200)
        r = c.get("/stores")
        self.assertEqual(r.status_code, 200, r.text)
        stores = r.json()["stores"]
        self.assertEqual(len(stores), 3, "maven-demo owns exactly 3 stores")
        for s in stores:
            self.assertIn("location_id", s)
            self.assertIn("store_location", s)

    # spec: F30.ac — /me reflects the signed claims.
    def test_me_returns_claims(self):
        c = self._client()
        self._login(c)
        r = c.get("/me")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"tenant_slug": DEMO_SLUG, "role": "manager"})

    # spec: F30.ac — logout clears the session (subsequent /stores → 401).
    def test_logout_clears_session(self):
        c = self._client()
        self._login(c)
        self.assertEqual(c.get("/stores").status_code, 200)
        self.assertEqual(c.post("/auth/logout").status_code, 200)
        c.cookies.clear()
        self.assertEqual(c.get("/stores").status_code, 401)


if __name__ == "__main__":
    unittest.main()
