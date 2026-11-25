"""CS-061c audit port contract."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
SCHEDULER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCHEDULER_ROOT))
from auth.repository import InMemoryAuthRepository
from auth.service import AuthenticationError, Principal, TenantAuthService, get_audit_failure_count

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
    def simulate_audit_outage(self):
        self.service = TenantAuthService.for_contract_fixture()
        audit = patch.object(self.service._repo, "record_audit", side_effect=RuntimeError("audit unavailable"))
        self.record_audit = audit.start()
        self.addCleanup(audit.stop)

    def login(self, password="correct horse battery staple"):
        return self.service.login_json(
            tenant_slug="maven-demo", email="owner@maven.example", password=password
        )

    def test_audit_failure_is_surfaced(self):
        self.simulate_audit_outage()
        with self.assertLogs("auth.service", level="ERROR") as logs:
            self.login()
        self.assertEqual([record.getMessage() for record in logs.records], ["audit.write.failed"])

    def test_login_succeeds_when_audit_store_is_down(self):
        self.simulate_audit_outage()
        tokens = self.login()
        self.assertTrue(tokens.access_token)
        self.assertTrue(tokens.refresh_token)

    def test_audit_failure_is_logged_and_counted(self):
        self.simulate_audit_outage()
        event = "auth.login.succeeded"
        before = get_audit_failure_count(event)
        with self.assertLogs("auth.service", level="ERROR") as logs:
            self.login()
            self.login()
        self.assertEqual(get_audit_failure_count(event), before + 2)
        self.assertEqual(len(logs.records), 2)
        for record in logs.records:
            self.assertEqual(record.levelname, "ERROR")
            self.assertEqual(record.getMessage(), "audit.write.failed")
            self.assertEqual(record.action, event)
            self.assertEqual(record.policy, "best_effort")
            self.assertIsInstance(record.exc_info[1], RuntimeError)

    def test_invitation_creation_still_fails_closed_when_audit_down(self):
        self.simulate_audit_outage()
        owner = Principal(
            InMemoryAuthRepository._FIXTURE_TENANT_ID,
            InMemoryAuthRepository._FIXTURE_USER_ID,
            "owner",
        )
        with patch.object(self.service._invitation_notifier, "notify") as notify:
            with self.assertRaisesRegex(RuntimeError, "audit unavailable"):
                self.service.create_invitation(owner, "audit@maven.example", "staff")
            notify.assert_not_called()
        self.assertEqual(self.record_audit.call_args.args[0].action, "invitation.created")

    def test_login_failure_keeps_original_error_when_audit_down(self):
        self.simulate_audit_outage()
        original = AuthenticationError("authentication failed")
        with patch.object(self.service, "_authenticate_credentials", side_effect=original):
            with self.assertRaises(AuthenticationError) as caught:
                self.login(password="wrong")
        self.assertIs(caught.exception, original)
        self.assertEqual(self.record_audit.call_args.args[0].action, "auth.login.failed")


if __name__ == "__main__":
    unittest.main()
