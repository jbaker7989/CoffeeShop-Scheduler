"""CS-061b / F61 AC3 per-request enforcement through the ASGI gateway; red before impl.

The gateway's protected dependency must authorize each request with the CS-061b
``authorize_access_request`` path, so a suspended tenant is rejected with 403 and a
revoked/deactivated principal with 401 on the very next request — while an active principal
still succeeds. Tenant identity still comes only from the reconciled claim.

The auth service is the in-memory contract fixture, so these run without a database or
secrets. Red now because the gateway is not yet wired to the (absent) CS-061b status /
revocation seams: an active token still returns 200, but a suspended or revoked token does
not yet get 403 / 401.
"""
from __future__ import annotations

import base64
import json
import sys
import unittest
from pathlib import Path


SCHEDULER_ROOT = Path(__file__).resolve().parents[1]
if str(SCHEDULER_ROOT) not in sys.path:
    sys.path.insert(0, str(SCHEDULER_ROOT))

FIXTURE_TENANT = "00000000-0000-0000-0000-000000000001"
LOGIN = {"tenant_slug": "maven-demo", "email": "owner@maven.example", "password": "correct horse battery staple"}


def _jti(access_token: str) -> str:
    payload = access_token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))["jti"]


class _StoresSpy:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, tenant_id: str) -> list[dict]:
        self.calls.append(tenant_id)
        return [{"location_id": 1, "store_location": "Demo"}]


class CS061bGatewayEnforcementTests(unittest.TestCase):
    def _app(self, service):
        from fastapi.testclient import TestClient
        from api.app import create_app

        spy = _StoresSpy()
        return TestClient(create_app(service, fetch_stores=spy)), spy

    def _token(self, client) -> str:
        r = client.post("/auth/login", json=LOGIN)
        self.assertEqual(200, r.status_code, r.text)
        return r.json()["access_token"]

    # spec: F61.ac3 — an active principal still authorizes protected routes (baseline).
    def test_active_principal_still_authorized(self) -> None:
        from auth.service import TenantAuthService

        client, spy = self._app(TenantAuthService.for_contract_fixture())
        token = self._token(client)
        r = client.get("/stores", headers={"authorization": f"Bearer {token}"})
        self.assertEqual(200, r.status_code, r.text)
        self.assertEqual([FIXTURE_TENANT], spy.calls)

    # spec: F61.ac3 — a suspended tenant gets 403 on the next protected request, and no
    # tenant data read is attempted.
    def test_suspended_tenant_gets_403(self) -> None:
        from auth_test_support import set_fixture_tenant_status
        from auth.service import TenantAuthService

        service = TenantAuthService.for_contract_fixture()
        client, spy = self._app(service)
        token = self._token(client)
        set_fixture_tenant_status(service, "suspended")
        r = client.get("/stores", headers={"authorization": f"Bearer {token}"})
        self.assertEqual(403, r.status_code, r.text)
        self.assertEqual([], spy.calls, "a suspended tenant must never reach the data read")

    # spec: F61.ac3 — a deactivated user gets 401 on the next protected request.
    def test_deactivated_user_gets_401(self) -> None:
        from auth_test_support import set_fixture_user_active
        from auth.service import TenantAuthService

        service = TenantAuthService.for_contract_fixture()
        client, spy = self._app(service)
        token = self._token(client)
        set_fixture_user_active(service, False)
        r = client.get("/stores", headers={"authorization": f"Bearer {token}"})
        self.assertEqual(401, r.status_code, r.text)
        self.assertEqual([], spy.calls)

    # spec: F61.ac3 — a token revoked by jti (single-session logout) gets 401 next request.
    def test_revoked_token_gets_401(self) -> None:
        from auth.service import TenantAuthService

        service = TenantAuthService.for_contract_fixture()
        client, spy = self._app(service)
        token = self._token(client)
        service.revoke_token(token)
        r = client.get("/me", headers={"authorization": f"Bearer {token}"})
        self.assertEqual(401, r.status_code, r.text)

    # spec: F61.ac3 — enforcement is transport-independent: a suspended tenant is 403 whether
    # the access credential arrives as a bearer header or the browser access cookie.
    def test_enforcement_applies_to_browser_cookie_transport(self) -> None:
        from auth_test_support import set_fixture_tenant_status
        from auth.service import TenantAuthService

        service = TenantAuthService.for_contract_fixture()
        client, _ = self._app(service)
        login = client.post("/auth/browser/login", json=LOGIN)
        access = login.cookies["access_token"]
        set_fixture_tenant_status(service, "suspended")
        r = client.get("/stores", headers={"cookie": f"access_token={access}"})
        self.assertEqual(403, r.status_code, r.text)


if __name__ == "__main__":
    unittest.main()
