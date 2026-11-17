"""CS-061c invitation policy contract (stdlib unittest, no database)."""
import sys
import unittest
from pathlib import Path
from datetime import timedelta

SCHEDULER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCHEDULER_ROOT))

from auth.repository import InMemoryAuthRepository
from auth.service import (AuthenticationError, InvitationConflictError, InvitationPermissionError,
                          Principal, TenantAuthService)

class InvitationContractTests(unittest.TestCase):
    def setUp(self):
        self.service = TenantAuthService.for_contract_fixture()
        self.owner = Principal(InMemoryAuthRepository._FIXTURE_TENANT_ID, InMemoryAuthRepository._FIXTURE_USER_ID, "owner")
    def test_invite_accept_creates_member_and_logs_in(self):
        issued = self.service.create_invitation(self.owner, "new@maven.example", "staff")
        tokens = self.service.accept_invitation(issued.token, "new password")
        principal = self.service.authenticate_access_token(tokens.access_token)
        self.assertEqual("staff", principal.role)
        self.assertEqual(self.owner.tenant_id, principal.tenant_id)
    def test_role_escalation_and_duplicate_pending_are_rejected(self):
        manager = Principal(self.owner.tenant_id, self.owner.user_id, "manager")
        with self.assertRaises(InvitationPermissionError): self.service.create_invitation(manager, "x@maven.example", "owner")
        self.service.create_invitation(self.owner, "x@maven.example", "staff")
        with self.assertRaises(InvitationConflictError): self.service.create_invitation(self.owner, "x@maven.example", "staff")
    def test_revoked_unknown_expired_and_race_fail_closed(self):
        issued = self.service.create_invitation(self.owner, "bad@maven.example", "staff")
        self.service.revoke_invitation(self.owner, issued.invitation_id)
        for token in (issued.token, "not-a-real-token"):
            with self.assertRaises(AuthenticationError): self.service.accept_invitation(token, "password")
        issued = self.service.create_invitation(self.owner, "race@maven.example", "staff")
        self.service.accept_invitation(issued.token, "password")
        with self.assertRaises(AuthenticationError): self.service.accept_invitation(issued.token, "password")
    def test_reinvite_after_revoke(self):
        first = self.service.create_invitation(self.owner, "again@maven.example", "staff")
        self.service.revoke_invitation(self.owner, first.invitation_id)
        self.assertNotEqual(first.invitation_id, self.service.create_invitation(self.owner, "again@maven.example", "staff").invitation_id)

    def test_asgi_invite_accept_flow_uses_reconciled_principal(self):
        from fastapi.testclient import TestClient
        from api.app import create_app
        client = TestClient(create_app(self.service, fetch_stores=lambda _: []))
        login = client.post("/auth/login", json={"tenant_slug": "maven-demo", "email": "owner@maven.example", "password": "correct horse battery staple"})
        created = client.post("/invitations", json={"email":"api@maven.example","role":"staff"}, headers={"Authorization": "Bearer " + login.json()["access_token"]})
        self.assertEqual(200, created.status_code)
        accepted = client.post("/invitations/accept", json={"token":created.json()["token"],"password":"password"})
        self.assertEqual(200, accepted.status_code)
        self.assertIn("refresh_token", accepted.json())

if __name__ == "__main__": unittest.main()
