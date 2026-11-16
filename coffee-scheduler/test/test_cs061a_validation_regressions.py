"""Independent CS-061a validation regressions.

These cases cover HTTP parser edges that the implementation contract tests do
not exercise through an ASGI client.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCHEDULER_ROOT = Path(__file__).resolve().parents[1]
if str(SCHEDULER_ROOT) not in sys.path:
    sys.path.insert(0, str(SCHEDULER_ROOT))


class BrowserRefreshCookieParsingRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from auth.api import create_auth_router
        from auth.service import TenantAuthService

        app = FastAPI()
        app.include_router(create_auth_router(TenantAuthService.for_contract_fixture()))
        self.client = TestClient(app)

    def _browser_login(self) -> str:
        response = self.client.post(
            "/auth/browser/login",
            json={
                "tenant_slug": "maven-demo",
                "email": "owner@maven.example",
                "password": "correct horse battery staple",
            },
        )
        self.assertEqual(200, response.status_code, response.text)
        return response.cookies["refresh_token"]

    # spec: F61.ac2 — a browser refresh credential has exactly one unambiguous source.
    def test_duplicate_refresh_cookie_is_rejected_even_when_one_value_is_valid(self) -> None:
        refresh_token = self._browser_login()
        response = self.client.post(
            "/auth/browser/refresh",
            headers={"cookie": f"refresh_token=attacker-value; refresh_token={refresh_token}"},
        )
        self.assertEqual(401, response.status_code, response.text)
        self.assertEqual({"detail": "authentication failed"}, response.json())

    # spec: F61.ac2 — a missing browser refresh cookie fails closed.
    def test_missing_refresh_cookie_is_rejected(self) -> None:
        response = self.client.post("/auth/browser/refresh")
        self.assertEqual(401, response.status_code, response.text)


class AuthDatabasePrincipalRegressionTests(unittest.TestCase):
    class _Cursor:
        def __init__(self, principal_row, direct_table_privilege=False) -> None:
            self.principal_row = principal_row
            self.direct_table_privilege = direct_table_privilege
            self._last_was_table_check = False

        def execute(self, query, params=None) -> None:
            self._last_was_table_check = "has_table_privilege" in query

        def fetchone(self):
            return (self.direct_table_privilege,) if self._last_was_table_check else self.principal_row

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    class _Connection:
        def __init__(self, cursor) -> None:
            self._cursor = cursor

        def cursor(self):
            return self._cursor

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    class _Pool:
        def __init__(self, principal_row, direct_table_privilege=False) -> None:
            self.closed = True
            self.opened = False
            self._connection = AuthDatabasePrincipalRegressionTests._Connection(
                AuthDatabasePrincipalRegressionTests._Cursor(principal_row, direct_table_privilege)
            )

        def open(self, *, wait):
            self.opened = True
            self.closed = False

        def connection(self):
            return self._connection

    # spec: F61.ac1 / BUG-035 — the gateway rejects superuser or direct-table auth DSNs.
    def test_auth_database_principal_must_be_constrained_auth_service_member(self) -> None:
        from api.auth_gateway import GatewayAuthConfigError, _validate_auth_database_principal

        for row, direct_table_privilege in (
            ((True, False, True), False),     # superuser
            ((False, True, True), False),     # BYPASSRLS
            ((False, False, False), False),   # not an auth_svc member
            ((False, False, True), True),     # direct credential-table access
        ):
            with self.assertRaises(GatewayAuthConfigError):
                _validate_auth_database_principal(self._Pool(row, direct_table_privilege))

        valid = self._Pool((False, False, True))
        _validate_auth_database_principal(valid)
        self.assertTrue(valid.opened)


if __name__ == "__main__":
    unittest.main()
