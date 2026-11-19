from __future__ import annotations

import sys
import unittest
from pathlib import Path
SCHEDULER_ROOT = Path(__file__).resolve().parents[1]
if str(SCHEDULER_ROOT) not in sys.path:
    sys.path.insert(0, str(SCHEDULER_ROOT))

class AccessTokenSingleValidationTests(unittest.TestCase):
    # spec: F61.ac3 / BUG-052 — authorization validates a token exactly once.
    def test_authorization_validates_token_once(self):
        from auth.service import TenantAuthService

        service = TenantAuthService.for_contract_fixture()
        token = service.login_json(tenant_slug="maven-demo", email="owner@maven.example", password="correct horse battery staple").access_token
        original = service._tokens.validate_access_token
        calls = []

        def counting_validate(value):
            calls.append(value)
            return original(value)

        service._tokens.validate_access_token = counting_validate
        service.authorize_access_request(token)
        self.assertEqual([token], calls)


