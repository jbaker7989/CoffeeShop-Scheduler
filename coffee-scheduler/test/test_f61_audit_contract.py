"""CS-061c audit port contract."""
import sys
import unittest
from pathlib import Path
SCHEDULER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCHEDULER_ROOT))
from auth.repository import InMemoryAuthRepository
from auth.service import AuthenticationError, Principal, TenantAuthService

class AuditContractTests(unittest.TestCase):
    def test_login_and_invitation_lifecycle_emit_audit(self):
        service = TenantAuthService.for_contract_fixture(); repo = service._repo
        with self.assertRaises(AuthenticationError): service.login_json(tenant_slug="maven-demo",email="owner@maven.example",password="wrong")
        service.login_json(tenant_slug="maven-demo",email="owner@maven.example",password="correct horse battery staple")
        owner=Principal(InMemoryAuthRepository._FIXTURE_TENANT_ID, InMemoryAuthRepository._FIXTURE_USER_ID,"owner")
        invite=service.create_invitation(owner,"audit@maven.example","staff")
        service.accept_invitation(invite.token,"password")
        actions=[x.action for x in repo.audit_entries]
        self.assertIn("auth.login.failed",actions); self.assertIn("auth.login.succeeded",actions)
        self.assertIn("invitation.created",actions); self.assertIn("invitation.accepted",actions)
    def test_audit_failure_is_surfaced(self):
        service=TenantAuthService.for_contract_fixture(); repo=service._repo
        repo.record_audit=lambda entry: (_ for _ in ()).throw(RuntimeError("audit unavailable"))
        with self.assertRaises(RuntimeError): service.login_json(tenant_slug="maven-demo",email="owner@maven.example",password="correct horse battery staple")

if __name__ == "__main__": unittest.main()
