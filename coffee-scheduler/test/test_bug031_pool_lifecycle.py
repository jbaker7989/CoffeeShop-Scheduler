"""BUG-031: the lazy gateway pool has an explicit ASGI lifecycle."""
from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from api.app import create_app
from auth.service import TenantAuthService


class GatewayPoolLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.auth = TenantAuthService.for_contract_fixture()

    # spec: F30.ac1 — gateway startup and liveness leave the lazy DB pool unopened.
    def test_default_database_pool_stays_lazy_through_startup_and_health(self) -> None:
        pool = Mock()
        fake_db = types.ModuleType("api.db")
        fake_db.pool = pool
        fake_db.fetch_stores = Mock(return_value=[])

        with patch.dict(sys.modules, {"api.db": fake_db}):
            app = create_app(self.auth)
            with TestClient(app) as client:
                self.assertEqual({"status": "ok"}, client.get("/healthz").json())
                pool.open.assert_not_called()
                pool.close.assert_not_called()
            pool.close.assert_called_once_with()

    # spec: F30.ac2 — shutdown closes the default pool even after request failure.
    def test_default_database_pool_closes_after_exceptional_request(self) -> None:
        pool = Mock()
        fake_db = types.ModuleType("api.db")
        fake_db.pool = pool
        fake_db.fetch_stores = Mock(return_value=[])

        with patch.dict(sys.modules, {"api.db": fake_db}):
            app = create_app(self.auth)

            @app.get("/explode")
            def explode() -> None:
                raise RuntimeError("expected test failure")

            with TestClient(app) as client:
                with self.assertRaises(RuntimeError):
                    client.get("/explode")
            pool.close.assert_called_once_with()

    # spec: F30.ac2 — isolated adapters do not close the process-wide production pool.
    def test_injected_data_adapter_does_not_import_or_close_production_pool(self) -> None:
        pool = Mock()
        fake_db = types.ModuleType("api.db")
        fake_db.pool = pool

        with patch.dict(sys.modules, {"api.db": fake_db}):
            app = create_app(self.auth, fetch_stores=lambda _tenant_id: [])
            with TestClient(app) as client:
                self.assertEqual(200, client.get("/healthz").status_code)
            pool.close.assert_not_called()


if __name__ == "__main__":
    unittest.main()
