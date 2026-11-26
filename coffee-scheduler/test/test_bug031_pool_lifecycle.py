"""Gateway lifespan and lazy PostgreSQL access (BUG-031, CS-106)."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.app import create_app
from auth.service import TenantAuthService


class GatewayPoolLifecycleTests(unittest.TestCase):
    # spec: F30.ac1 — startup, liveness and idle shutdown never allocate a pool.
    def test_idle_gateway(self):
        with patch("api.db.ConnectionPool") as pool_factory:
            app = create_app(TenantAuthService.for_contract_fixture())
            with TestClient(app) as client:
                self.assertEqual({"status": "ok"}, client.get("/healthz").json())
            pool_factory.assert_not_called()

    # spec: F30.ac2 — an exception during the lifespan still releases the owned adapter.
    def test_exceptional_shutdown(self):
        with patch("api.db.GatewayDatabase", autospec=True) as database:
            app = create_app(TenantAuthService.for_contract_fixture())
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                with TestClient(app):
                    raise RuntimeError("interrupted")
            database.return_value.close.assert_called_once_with()

    # spec: F30.ac2 — fully injected accessors work even without the production module.
    def test_injected_access(self):
        with patch.dict(sys.modules, {"api.db": None}):
            app = create_app(TenantAuthService.for_contract_fixture(), fetch_stores=lambda _: [])
            with TestClient(app) as client:
                self.assertEqual(200, client.get("/healthz").status_code)


if __name__ == "__main__":
    unittest.main()
