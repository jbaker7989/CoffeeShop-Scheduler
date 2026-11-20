"""CS-062 trial status must authorize while suspended/churned do not."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCHEDULER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCHEDULER_ROOT))


class TrialAccessTests(unittest.TestCase):
    def test_trial_authorizes_me_and_stores_but_lifecycle_states_are_forbidden(self) -> None:
        from api.app import create_app
        from auth.repository import TenantUserRecord
        from auth.service import TenantAuthService
        from fastapi.testclient import TestClient

        service = TenantAuthService.for_contract_fixture()
        repo = service._repo
        tenant_id = "00000000-0000-0000-0000-000000000062"
        user_id = "00000000-0000-0000-0000-000000000063"
        repo.add_user(TenantUserRecord(user_id, tenant_id, "trial-roast", "owner@trial.example", service._passwords.hash("trial password"), "owner", True))
        repo.set_tenant_status(tenant_id, "trial")
        client = TestClient(create_app(service, fetch_stores=lambda tid: [{"tenant": tid}]))
        login = client.post("/auth/login", json={"tenant_slug": "trial-roast", "email": "owner@trial.example", "password": "trial password"})
        self.assertEqual(200, login.status_code, login.text)
        headers = {"authorization": f"Bearer {login.json()['access_token']}"}
        self.assertEqual(200, client.get("/me", headers=headers).status_code)
        self.assertEqual(200, client.get("/stores", headers=headers).status_code)
        for state in ("suspended", "churned"):
            repo.set_tenant_status(tenant_id, state)
            self.assertEqual(403, client.get("/me", headers=headers).status_code)
            self.assertEqual(403, client.get("/stores", headers=headers).status_code)


if __name__ == "__main__":
    unittest.main()
