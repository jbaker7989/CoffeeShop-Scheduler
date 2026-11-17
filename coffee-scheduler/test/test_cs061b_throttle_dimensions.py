"""CS-061b throttle dimensions and runtime configuration regression coverage."""
from __future__ import annotations
import sys, unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))

class ThrottleDimensions(unittest.TestCase):
    def test_ip_and_account_dimensions_are_independent_and_expire(self):
        from auth.repository import InMemoryAuthRepository
        repo=InMemoryAuthRepository(); now=datetime(2026,1,1,tzinfo=timezone.utc)
        # One IP distributed over identities hits the independent IP throttle.
        for i in range(3): repo.record_login_attempt('t',f'u{i}@x','203.0.113.7',False,now,3,30)
        self.assertTrue(repo.login_locked('t','innocent@x','203.0.113.7',now,3,30))
        self.assertFalse(repo.login_locked('t','innocent@x','203.0.113.8',now,3,30))
        # A distributed-IP attack on one account separately hits account state.
        for i in range(3): repo.record_login_attempt('t','victim@x',f'203.0.113.{20+i}',False,now,3,30)
        self.assertTrue(repo.login_locked('t','victim@x','203.0.113.99',now,3,30))
        self.assertFalse(repo.login_locked('t','victim@x','203.0.113.99',now+timedelta(seconds=31),3,30))

    def test_runtime_maximum_changes_service_enforcement(self):
        from auth.service import TenantAuthService, AuthenticationError
        service=TenantAuthService.for_contract_fixture()
        service._throttle_max_failures=2; service._throttle_window_seconds=60
        for _ in range(2):
            with self.assertRaises(AuthenticationError): service.login_json(tenant_slug='maven-demo',email='owner@maven.example',password='wrong',client_ip='198.51.100.9')
        from auth.ratelimit import AccountLockedError
        with self.assertRaises(AccountLockedError): service.login_json(tenant_slug='maven-demo',email='owner@maven.example',password='correct horse battery staple',client_ip='198.51.100.9')

if __name__ == '__main__': unittest.main()
