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


if __name__ == "__main__":
    unittest.main()
