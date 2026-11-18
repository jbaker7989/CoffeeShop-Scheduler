"""BUG-035 — CS-061a gateway integration contracts, through an ASGI client, no database.

Proves the reachable gateway actually mounts the CS-061a auth boundary: exactly one owner per
/auth route (no legacy duplicate), JWKS served, JSON vs browser transport separation, protected
endpoints authenticated from reconciled access-token claims, malformed/multiple/missing/ambiguous
credentials rejected, and a token's tenant claim unoverridable by request input. The auth service
is the in-memory contract fixture, so these run without a database or secrets.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCHEDULER_ROOT = Path(__file__).resolve().parents[1]
if str(SCHEDULER_ROOT) not in sys.path:
    sys.path.insert(0, str(SCHEDULER_ROOT))

FIXTURE_TENANT = "00000000-0000-0000-0000-000000000001"
OTHER_TENANT = "00000000-0000-0000-0000-000000000002"
LOGIN = {"tenant_slug": "maven-demo", "email": "owner@maven.example", "password": "correct horse battery staple"}


class _StoresSpy:
    """Records the tenant_id the gateway passes into the RLS data read."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, tenant_id: str) -> list[dict]:
        self.calls.append(tenant_id)
        return [{"location_id": 1, "store_location": "Demo"}]


class GatewayIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        from fastapi.testclient import TestClient
        from auth.service import TenantAuthService
        from api.app import create_app

        self.service = TenantAuthService.for_contract_fixture()
        self.spy = _StoresSpy()
        self.app = create_app(self.service, fetch_stores=self.spy)
        self.client = TestClient(self.app)

    # ----- helpers -----
    def _json_login(self) -> dict:
        r = self.client.post("/auth/login", json=LOGIN)
        self.assertEqual(200, r.status_code, r.text)
        return r.json()

    def _bearer(self) -> dict:
        return {"authorization": f"Bearer {self._json_login()['access_token']}"}

    # ----- route ownership / no legacy duplicate -----
    # spec: BUG-035 — exactly one handler owns /auth/login, and it is the CS-061a token route.
    def test_single_auth_login_route_is_cs061a_not_legacy_demo(self) -> None:
        login_routes = [r for r in self.app.routes if getattr(r, "path", None) == "/auth/login"]
        self.assertEqual(1, len(login_routes), "exactly one /auth/login handler (no legacy duplicate)")
        body = self._json_login()
        # CS-061a token shape, NOT the legacy {"role","tenant_slug"} + session cookie.
        self.assertEqual({"access_token", "token_type", "expires_in", "refresh_token"}, set(body))
        self.assertNotIn("session", self.client.cookies)

    # spec: BUG-035 — JWKS is reachable and public-only.
    def test_jwks_served_public_only(self) -> None:
        r = self.client.get("/.well-known/jwks.json")
        self.assertEqual(200, r.status_code, r.text)
        keys = r.json()["keys"]
        self.assertTrue(keys and all(k.get("kid") and k["kty"] == "RSA" for k in keys))
        for k in keys:  # no private RSA fields ever
            self.assertEqual(set(k) & {"d", "p", "q", "dp", "dq", "qi"}, set())

    # ----- transport separation -----
    # spec: F61.ac2 / BUG-035 — JSON login returns tokens in the body and sets no cookie.
    def test_json_login_sets_no_cookie(self) -> None:
        r = self.client.post("/auth/login", json=LOGIN)
        self.assertEqual(200, r.status_code)
        self.assertNotIn("set-cookie", {k.lower() for k in r.headers})

    # spec: F61.ac2 / BUG-035 — browser login returns metadata only and cookie-borne credentials.
    def test_browser_login_is_cookie_only(self) -> None:
        r = self.client.post("/auth/browser/login", json=LOGIN)
        self.assertEqual(200, r.status_code, r.text)
        self.assertEqual({"token_type", "expires_in"}, set(r.json()))
        self.assertNotIn("access_token", r.json())
        self.assertIn("access_token", r.cookies)
        self.assertIn("refresh_token", r.cookies)
        set_cookie = " ".join(r.headers.get_list("set-cookie")).lower()
        self.assertIn("httponly", set_cookie)
        self.assertIn("samesite=strict", set_cookie)
        self.assertIn("path=/auth/browser", set_cookie)

    # ----- protected endpoints authenticate from reconciled claims -----
    # spec: BUG-035 — a valid bearer access token authenticates /stores and /me from reconciled claims.
    def test_bearer_access_token_authenticates_protected_routes(self) -> None:
        headers = self._bearer()
        r = self.client.get("/stores", headers=headers)
        self.assertEqual(200, r.status_code, r.text)
        self.assertEqual([FIXTURE_TENANT], self.spy.calls, "tenant_id must be the reconciled claim")
        me = self.client.get("/me", headers=headers)
        self.assertEqual(200, me.status_code)
        self.assertEqual(FIXTURE_TENANT, me.json()["tenant_id"])
        self.assertEqual("owner", me.json()["role"])

    # spec: BUG-035 — the browser access cookie authenticates protected routes.
    def test_browser_access_cookie_authenticates_stores(self) -> None:
        login = self.client.post("/auth/browser/login", json=LOGIN)
        access = login.cookies["access_token"]
        r = self.client.get("/stores", headers={"cookie": f"access_token={access}"})
        self.assertEqual(200, r.status_code, r.text)
        self.assertEqual([FIXTURE_TENANT], self.spy.calls)

    # ----- credential edge cases (fail closed) -----
    # spec: BUG-035 — missing credential → 401.
    def test_stores_requires_credential(self) -> None:
        self.assertEqual(401, self.client.get("/stores").status_code)
        self.assertEqual([], self.spy.calls)

    # spec: BUG-035 — malformed bearer schemes/empty tokens → 401.
    def test_malformed_bearer_rejected(self) -> None:
        for value in ("Bearer", "Bearer ", "Basic abc", "token-without-scheme", "Bearer  "):
            r = self.client.get("/stores", headers={"authorization": value})
            self.assertEqual(401, r.status_code, f"{value!r} must be rejected")
        self.assertEqual([], self.spy.calls)

    # spec: BUG-035 — a bearer AND a cookie at once is ambiguous → 401.
    def test_ambiguous_bearer_plus_cookie_rejected(self) -> None:
        token = self._json_login()["access_token"]
        r = self.client.get(
            "/stores",
            headers={"authorization": f"Bearer {token}", "cookie": f"access_token={token}"},
        )
        self.assertEqual(401, r.status_code, r.text)

    # spec: BUG-035 — duplicate access_token cookies are ambiguous → 401 even if one is valid.
    def test_duplicate_access_cookie_rejected(self) -> None:
        token = self._json_login()["access_token"]
        r = self.client.get("/stores", headers={"cookie": f"access_token=attacker; access_token={token}"})
        self.assertEqual(401, r.status_code, r.text)

    # spec: BUG-035 — multiple Authorization headers → 401.
    def test_multiple_authorization_headers_rejected(self) -> None:
        token = self._json_login()["access_token"]
        r = self.client.get(
            "/stores",
            headers=[("authorization", f"Bearer {token}"), ("authorization", "Bearer other")],
        )
        self.assertEqual(401, r.status_code, r.text)

    # ----- tenant authority -----
    # spec: F61.ac2 / BUG-035 — a validly signed token for a non-member tenant is rejected.
    def test_cross_tenant_token_rejected(self) -> None:
        from auth_test_support import issue_contract_token

        forged = issue_contract_token(
            self.service, tenant_id=OTHER_TENANT, user_id=FIXTURE_TENANT, role="owner"
        )
        r = self.client.get("/stores", headers={"authorization": f"Bearer {forged}"})
        self.assertEqual(401, r.status_code, r.text)
        self.assertEqual([], self.spy.calls)

    # spec: BUG-035 — request input cannot override the token's tenant claim.
    def test_request_tenant_input_cannot_override_claim(self) -> None:
        headers = self._bearer()
        r = self.client.get(f"/stores?tenant_id={OTHER_TENANT}", headers=headers)
        self.assertEqual(200, r.status_code, r.text)
        self.assertEqual([FIXTURE_TENANT], self.spy.calls, "query tenant_id must be ignored")


if __name__ == "__main__":
    unittest.main()
