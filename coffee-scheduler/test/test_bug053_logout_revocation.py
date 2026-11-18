from __future__ import annotations

import sys
import unittest
from pathlib import Path
SCHEDULER_ROOT = Path(__file__).resolve().parents[1]
if str(SCHEDULER_ROOT) not in sys.path:
    sys.path.insert(0, str(SCHEDULER_ROOT))

class LogoutRevocationTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from api.app import create_app
        from auth.service import TenantAuthService

        self.service = TenantAuthService.for_contract_fixture()
        self.client = TestClient(create_app(self.service, fetch_stores=lambda tenant_id: []))

    def _login(self):
        response = self.client.post("/auth/browser/login", json={
            "tenant_slug": "maven-demo", "email": "owner@maven.example",
            "password": "correct horse battery staple",
        })
        self.assertEqual(200, response.status_code, response.text)
        return response.cookies["access_token"], response.cookies["refresh_token"]

    def test_browser_logout_revokes_access_and_refresh_family(self):
        access, refresh = self._login()
        response = self.client.post("/auth/logout", headers={
            "cookie": f"access_token={access}; refresh_token={refresh}"
        })
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual(401, self.client.get("/me", headers={"authorization": f"Bearer {access}"}).status_code)
        self.assertEqual(401, self.client.post("/auth/refresh", json={"refresh_token": refresh}).status_code)
        raw = " ".join(response.headers.get_list("set-cookie")).lower()
        self.assertIn("path=/auth/browser", raw)
        self.assertIn("access_token=", raw)
        self.assertIn("refresh_token=", raw)

    def test_bearer_logout_revokes_access(self):
        access, _refresh = self._login()
        response = self.client.post("/auth/logout", headers={"authorization": f"Bearer {access}"})
        self.assertEqual(200, response.status_code)
        self.assertEqual(401, self.client.get("/me", headers={"authorization": f"Bearer {access}"}).status_code)

    def test_missing_invalid_or_ambiguous_credentials_clear_cookies(self):
        for headers in ({}, {"authorization": "Bearer invalid"}, {
            "authorization": "Bearer one", "cookie": "access_token=two"
        }, {"cookie": "access_token=one; access_token=two"}):
            response = self.client.post("/auth/logout", headers=headers)
            self.assertEqual(200, response.status_code, response.text)
            raw = " ".join(response.headers.get_list("set-cookie")).lower()
            self.assertIn("access_token=", raw)
            self.assertIn("refresh_token=", raw)

    def test_malformed_refresh_cookie_does_not_prevent_access_revocation(self):
        access, _refresh = self._login()
        response = self.client.post("/auth/logout", headers={
            "cookie": f"access_token={access}; refresh_token={'x' * 5000}"
        })
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual(401, self.client.get("/me", headers={"authorization": f"Bearer {access}"}).status_code)

    def test_valid_revocation_uses_canonical_identity_and_claim_jti_expiry(self):
        from auth.service import TenantAuthService
        from datetime import datetime, timezone
        from auth_test_support import issue_contract_token
        from unittest.mock import Mock

        token = issue_contract_token(self.service)
        repository = self.service._repo
        original = repository.revoke_access_token
        calls = []
        def record(jti, tenant_id, user_id, expires_at):
            calls.append((jti, tenant_id, user_id, expires_at))
            original(jti, tenant_id, user_id, expires_at)
        repository.revoke_access_token = record
        self.service.revoke_token(token)
        self.assertEqual(1, len(calls))
        jti, tenant, user, expiry = calls[0]
        self.assertNotEqual("00000000-0000-0000-0000-000000000001", jti)  # token-specific JTI
        self.assertEqual((tenant, user), ("00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000001"))
        self.assertEqual(timezone.utc, expiry.tzinfo)
        self.assertGreater(expiry, datetime.now(timezone.utc))
