"""F61.ac5: invitation-accept throttling is canonical, isolated, and race-safe."""
from __future__ import annotations

import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

SCHEDULER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCHEDULER_ROOT))

from fastapi.testclient import TestClient

from api.app import create_app
from auth.ratelimit import MAX_FAILED_ATTEMPTS
from auth.repository import InMemoryAuthRepository
from auth.service import TenantAuthService


class InvitationThrottleRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = InMemoryAuthRepository()
        self.now = datetime(2026, 1, 1, tzinfo=UTC)

    # spec: F61.ac5 — different IPs have independent durable-equivalent throttle buckets.
    def test_ip_buckets_are_isolated_and_success_clears_only_its_ip(self) -> None:
        for _ in range(3):
            self.assertFalse(self.repo.begin_invitation_accept(
                "203.0.113.10", self.now, 3, 60, 60,
            ))
        self.assertTrue(self.repo.begin_invitation_accept("203.0.113.10", self.now, 3, 60, 60))
        for _ in range(2):
            self.assertFalse(self.repo.begin_invitation_accept(
                "203.0.113.11", self.now, 3, 60, 60,
            ))
        self.repo.complete_invitation_accept("203.0.113.11", succeeded=True, when=self.now)
        self.assertTrue(self.repo.begin_invitation_accept("203.0.113.10", self.now, 3, 60, 60))
        self.assertFalse(self.repo.begin_invitation_accept("203.0.113.11", self.now, 3, 60, 60))

    # spec: F61.ac5 — threshold reservation is atomic and expired locks reset at their boundary.
    def test_concurrent_reservations_and_expiry(self) -> None:
        with ThreadPoolExecutor(max_workers=16) as workers:
            locked = list(workers.map(
                lambda _: self.repo.begin_invitation_accept("203.0.113.20", self.now, 5, 60, 30),
                range(20),
            ))
        self.assertEqual(5, locked.count(False))
        self.assertEqual(15, locked.count(True))
        self.assertFalse(self.repo.begin_invitation_accept(
            "203.0.113.20", self.now + timedelta(seconds=30), 5, 60, 30,
        ))

    # spec: F61.ac5 — IPv6 equivalents share state; malformed addresses fail closed.
    def test_ip_is_canonicalized_and_invalid_ip_is_rejected(self) -> None:
        self.assertFalse(self.repo.begin_invitation_accept(
            "2001:0db8:0:0:0:0:0:1", self.now, 1, 60, 60,
        ))
        self.assertTrue(self.repo.begin_invitation_accept(
            "2001:db8::1", self.now, 1, 60, 60,
        ))
        with self.assertRaises(ValueError):
            self.repo.begin_invitation_accept("not-an-ip", self.now, 5, 60, 60)


class InvitationThrottleGatewayTests(unittest.TestCase):
    # spec: F61.ac5 — independent gateway apps share repository throttle state.
    def test_second_app_observes_locked_ip_but_other_ip_can_accept(self) -> None:
        service = TenantAuthService.for_contract_fixture()
        from auth.repository import InMemoryAuthRepository as FixtureRepository
        from auth.service import Principal

        principal = Principal(
            FixtureRepository._FIXTURE_TENANT_ID,
            FixtureRepository._FIXTURE_USER_ID,
            "owner",
        )
        invitation = service.create_invitation(principal, "different-ip@maven.example", "staff")
        app_a = create_app(service, fetch_stores=lambda _tenant: [])
        app_b = create_app(service, fetch_stores=lambda _tenant: [])
        client_a = TestClient(app_a, client=("203.0.113.30", 41000))
        client_b_same_ip = TestClient(app_b, client=("203.0.113.30", 42000))
        client_b_other_ip = TestClient(app_b, client=("203.0.113.31", 42001))

        for _ in range(MAX_FAILED_ATTEMPTS):
            response = client_a.post(
                "/invitations/accept", json={"token": "invalid", "password": "password"},
            )
            self.assertEqual(401, response.status_code)
        locked = client_b_same_ip.post(
            "/invitations/accept", json={"token": "invalid", "password": "password"},
        )
        self.assertEqual(429, locked.status_code)
        accepted = client_b_other_ip.post(
            "/invitations/accept", json={"token": invitation.token, "password": "password"},
        )
        self.assertEqual(200, accepted.status_code)


if __name__ == "__main__":
    unittest.main()
