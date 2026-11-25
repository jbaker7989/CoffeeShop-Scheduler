"""BUG-033 regressions: retired demo credentials must never be a production auth path."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class Bug033DemoAuthCloseoutTests(unittest.TestCase):
    def test_config_has_no_known_password_or_plaintext_fallback(self):
        source = (ROOT / "api" / "config.py").read_text(encoding="utf-8")
        self.assertNotIn("demo-pass-change-me", source)
        self.assertNotIn("DEMO_MANAGER_PASSWORD =", source)
        self.assertIn('DEMO_MANAGER_PASSWORD_HASH = os.environ.get("DEMO_MANAGER_PASSWORD_HASH")', source)

    def test_production_app_does_not_mount_legacy_demo_login(self):
        from auth.service import TenantAuthService
        from api.app import create_app

        app = create_app(TenantAuthService.for_contract_fixture())
        routes = [route for route in app.routes if getattr(route, "path", None) == "/auth/login"
                  and "POST" in (getattr(route, "methods", None) or set())]
        self.assertEqual(1, len(routes))
        self.assertEqual("login", getattr(routes[0].endpoint, "__name__", None))
        self.assertEqual("auth.api", routes[0].endpoint.__module__)

    def test_auth_startup_requires_real_cs061a_configuration_not_legacy_demo_fields(self):
        from api.auth_gateway import GatewayAuthConfigError, build_auth_service_from_env

        for env in (
            {"DEMO_MANAGER_PASSWORD_HASH": "legacy-hash", "DEMO_MANAGER_PASSWORD": "demo-pass-change-me"},
            {"COOKIE_SECURE": "true"},
        ):
            with self.subTest(env=env), self.assertRaises(GatewayAuthConfigError):
                build_auth_service_from_env(env)


if __name__ == "__main__":
    unittest.main()
