"""BUG-036 — manager-UI ↔ CS-061a browser-auth compatibility, through an ASGI client, no database.

Proves the cutover's UI contract: a browser login sets the HttpOnly cookies (no token in the body),
the manager pages/APIs authenticate by forwarding ONLY the ``access_token`` cookie exactly as the
Next server components do, the refresh cookie is scoped to ``/auth/browser`` (so the same-origin
rewrite reaches it), logout clears both cookies, the retired ``session`` cookie is never accepted,
and a request cannot override the claim-derived tenant. The auth service is the in-memory CS-061a
fixture, so this runs without a database or secrets.
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
    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, tenant_id: str) -> list[dict]:
        self.calls.append(tenant_id)
        return [{"location_id": 1, "store_location": "Demo"}]


class ManagerUiBrowserCompatTests(unittest.TestCase):
    def setUp(self) -> None:
        from fastapi.testclient import TestClient
        from auth.service import TenantAuthService
        from api.app import create_app

        self.service = TenantAuthService.for_contract_fixture()
        self.spy = _StoresSpy()
        self.app = create_app(self.service, fetch_stores=self.spy)
        # Do not persist cookies in the jar: simulate the Next server forwarding cookies explicitly.
        self.client = TestClient(self.app)

    def _browser_login(self):
        r = self.client.post("/auth/browser/login", json=LOGIN)
        self.assertEqual(200, r.status_code, r.text)
        return r

    # spec: BUG-036 — browser login returns metadata only; both credentials are HttpOnly cookies.
    def test_login_sets_httponly_cookies_no_token_in_body(self) -> None:
        r = self._browser_login()
        self.assertEqual({"token_type", "expires_in"}, set(r.json()))
        self.assertNotIn("access_token", r.json())
        self.assertNotIn("refresh_token", r.json())
        raw = r.headers.get_list("set-cookie")
        access = next(c for c in raw if c.startswith("access_token="))
        refresh = next(c for c in raw if c.startswith("refresh_token="))
        for cookie in (access, refresh):
            self.assertIn("httponly", cookie.lower())
            self.assertIn("samesite=strict", cookie.lower())
        self.assertIn("path=/;", access.lower() + ";")
        # Refresh cookie is scoped to /auth/browser so the same-origin rewrite reaches it (BUG-036).
        self.assertIn("path=/auth/browser", refresh.lower())

    # spec: BUG-036 — the manager layout/dashboard authenticate by forwarding only the access cookie.
    def test_forwarded_access_cookie_reaches_me_and_stores(self) -> None:
        access = self._browser_login().cookies["access_token"]
        headers = {"cookie": f"access_token={access}"}
        me = self.client.get("/me", headers=headers)
        self.assertEqual(200, me.status_code, me.text)
        self.assertEqual({"tenant_id", "user_id", "role"}, set(me.json()))
        self.assertEqual(FIXTURE_TENANT, me.json()["tenant_id"])
        stores = self.client.get("/stores", headers=headers)
        self.assertEqual(200, stores.status_code, stores.text)
        self.assertEqual([FIXTURE_TENANT], self.spy.calls)

    # spec: BUG-036 — the retired legacy `session` cookie authenticates nothing.
    def test_legacy_session_cookie_is_rejected(self) -> None:
        for path in ("/me", "/stores"):
            r = self.client.get(path, headers={"cookie": "session=anything-at-all"})
            self.assertEqual(401, r.status_code, f"{path} must reject a legacy session cookie")
        self.assertEqual([], self.spy.calls)

    # spec: BUG-036 — missing credential fails closed (the UI then redirects to /login).
    def test_missing_cookie_fails_closed(self) -> None:
        self.assertEqual(401, self.client.get("/me").status_code)
        self.assertEqual(401, self.client.get("/stores").status_code)

    # spec: BUG-036 — the browser refresh cookie rotates; a replay is rejected (cookie transport).
    def test_browser_refresh_rotates_and_detects_replay(self) -> None:
        login = self._browser_login()
        refresh = login.cookies["refresh_token"]
        rotated = self.client.post("/auth/browser/refresh", headers={"cookie": f"refresh_token={refresh}"})
        self.assertEqual(200, rotated.status_code, rotated.text)
        self.assertIn("access_token", rotated.cookies)
        self.assertIn("refresh_token", rotated.cookies)
        replay = self.client.post("/auth/browser/refresh", headers={"cookie": f"refresh_token={refresh}"})
        self.assertEqual(401, replay.status_code, replay.text)

    # spec: BUG-036 — logout clears both browser cookies.
    def test_logout_clears_browser_cookies(self) -> None:
        raw = " ".join(self.client.post("/auth/logout").headers.get_list("set-cookie")).lower()
        self.assertIn("access_token=", raw)
        self.assertIn("refresh_token=", raw)
        # Deletion is a past/zero expiry on both cookie scopes.
        self.assertTrue("max-age=0" in raw or "expires=thu, 01 jan 1970" in raw)
        self.assertIn("path=/auth/browser", raw)

    # spec: BUG-036 — a request cannot override the claim-derived tenant.
    def test_request_cannot_override_claim_tenant(self) -> None:
        access = self._browser_login().cookies["access_token"]
        r = self.client.get(f"/stores?tenant_id={OTHER_TENANT}", headers={"cookie": f"access_token={access}"})
        self.assertEqual(200, r.status_code, r.text)
        self.assertEqual([FIXTURE_TENANT], self.spy.calls, "query tenant_id must be ignored")


if __name__ == "__main__":
    unittest.main()
