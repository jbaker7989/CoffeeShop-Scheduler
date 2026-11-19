"""BUG-045 regression: auth routes receive only the trusted client IP."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCHEDULER_ROOT = Path(__file__).resolve().parents[1]
if str(SCHEDULER_ROOT) not in sys.path:
    sys.path.insert(0, str(SCHEDULER_ROOT))


LOGIN = {"tenant_slug": "maven-demo", "email": "owner@maven.example", "password": "correct horse battery staple"}


class ClientIpRegressionTests(unittest.TestCase):
    def _client(self, *, peer=("203.0.113.8", 4444), trusted=("203.0.113.0/24",)):
        from api.app import create_app
        from api.routing import TenantRoutingConfig
        from auth.service import TenantAuthService
        from fastapi.testclient import TestClient

        self.service = TenantAuthService.for_contract_fixture()
        app = create_app(
            self.service,
            fetch_stores=lambda _: [],
            routing_config=TenantRoutingConfig(
                mode="production", platform_suffix="coffeescheduler.com", trusted_proxy_cidrs=trusted,
            ),
            tenant_lookup=lambda _: "00000000-0000-0000-0000-000000000001",
        )
        return TestClient(app, base_url="https://maven-demo.coffeescheduler.com", client=peer)

    def test_trusted_xff_is_used_for_audit_not_zero_address(self) -> None:
        client = self._client()
        response = client.post("/auth/login", json=LOGIN, headers={"x-forwarded-for": "198.51.100.10"})
        self.assertEqual(200, response.status_code, response.text)
        entries = self.service._repo.audit_entries  # contract fixture exposes its captured audit port
        self.assertEqual("198.51.100.10", entries[-1].ip)
        self.assertNotEqual("0.0.0.0", entries[-1].ip)

    def test_untrusted_xff_is_ignored_and_direct_peer_is_audited(self) -> None:
        client = self._client(peer=("198.51.100.44", 4444))
        response = client.post("/auth/login", json=LOGIN, headers={"x-forwarded-for": "192.0.2.77"})
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual("198.51.100.44", self.service._repo.audit_entries[-1].ip)

    def test_distinct_trusted_clients_do_not_share_login_ip_bucket(self) -> None:
        client = self._client()
        # Four failures per real IP remain below the five-attempt IP threshold.  Before
        # BUG-045 both streams were recorded as 0.0.0.0 and the sixth request locked
        # every account under the tenant-wide shared bucket.
        for ip, email in (("198.51.100.10", "one@example.test"), ("198.51.100.11", "two@example.test")):
            for _ in range(4):
                response = client.post("/auth/login", json={**LOGIN, "email": email, "password": "wrong"}, headers={"x-forwarded-for": ip})
                self.assertEqual(401, response.status_code)
        first = client.post("/auth/login", json={**LOGIN, "email": "one@example.test", "password": "wrong"}, headers={"x-forwarded-for": "198.51.100.10"})
        second = client.post("/auth/login", json={**LOGIN, "email": "two@example.test", "password": "wrong"}, headers={"x-forwarded-for": "198.51.100.11"})
        self.assertEqual(401, first.status_code)
        self.assertEqual(401, second.status_code)

    def test_refresh_replay_audit_receives_trusted_client_ip(self) -> None:
        client = self._client()
        login = client.post("/auth/login", json=LOGIN, headers={"x-forwarded-for": "198.51.100.21"}).json()
        first = client.post("/auth/refresh", json={"refresh_token": login["refresh_token"]}, headers={"x-forwarded-for": "198.51.100.21"})
        self.assertEqual(200, first.status_code)
        replay = client.post("/auth/refresh", json={"refresh_token": login["refresh_token"]}, headers={"x-forwarded-for": "198.51.100.22"})
        self.assertEqual(401, replay.status_code)
        self.assertEqual("198.51.100.22", self.service._repo.audit_entries[-1].ip)


if __name__ == "__main__":
    unittest.main()
