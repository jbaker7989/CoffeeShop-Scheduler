"""CS-061b / F61 AC3 per-request revocation contracts; red before implementation.

Suspension and deactivation must take effect on the *next* protected request — not only
after the 15-minute access TTL — backed by a revocation list (per-jti) and a per-principal
session cutoff (``sessions_revoked_at``). These extend the CS-061a service without changing
``authenticate_access_token``; the new entry point is ``authorize_access_request``.

No database or secrets: the in-memory ``TenantAuthService.for_contract_fixture`` fixture is
extended with CS-061b knobs (``tenant_status``, ``sessions_revoked_at``) and revocation
seams. Red now because those knobs, the ``authorize_access_request`` method, the
``auth.revocation`` module, and ``TenantSuspendedError``/``SessionRevokedError`` do not exist.
"""
from __future__ import annotations

import base64
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path


SCHEDULER_ROOT = Path(__file__).resolve().parents[1]
if str(SCHEDULER_ROOT) not in sys.path:
    sys.path.insert(0, str(SCHEDULER_ROOT))

LOGIN = {"tenant_slug": "maven-demo", "email": "owner@maven.example", "password": "correct horse battery staple"}
FIXTURE_TENANT = "00000000-0000-0000-0000-000000000001"
FIXTURE_USER = "00000000-0000-0000-0000-000000000001"


def _claims(access_token: str) -> dict:
    payload = access_token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


class F61RevocationStatusRecheckTests(unittest.TestCase):
    # spec: F61.ac3 — an active-tenant, active-user access token authorizes a protected
    # request and reconciles to the canonical principal (the happy path for the new entry).
    def test_active_principal_authorizes_request(self) -> None:
        from auth.service import TenantAuthService

        service = TenantAuthService.for_contract_fixture()
        token = service.login_json(**LOGIN).access_token
        principal = service.authorize_access_request(token)
        self.assertEqual(FIXTURE_TENANT, principal.tenant_id)
        self.assertEqual("owner", principal.role)

    # spec: F61.ac3 — a suspended (non-active) tenant is refused per request, distinctly
    # from an ordinary auth failure so the gateway can map it to 403.
    def test_suspended_tenant_refused_per_request(self) -> None:
        from auth.service import TenantAuthService, TenantSuspendedError

        for status in ("suspended", "churned", "trial"):
            service = TenantAuthService.for_contract_fixture(tenant_status=status)
            token = service.login_json(**LOGIN).access_token
            with self.assertRaises(TenantSuspendedError, msg=status):
                service.authorize_access_request(token)

    # spec: F61.ac3 — a token minted while the tenant was active is rejected on the NEXT
    # request after suspension, well inside the access-token TTL (not deferred to expiry).
    def test_token_minted_before_suspension_rejected_next_request(self) -> None:
        from auth.service import TenantAuthService, TenantSuspendedError

        service = TenantAuthService.for_contract_fixture()
        token = service.login_json(**LOGIN).access_token
        # Token is still well within its 900s lifetime.
        self.assertGreater(_claims(token)["exp"], datetime.now(timezone.utc).timestamp())
        service.set_tenant_status("suspended")
        with self.assertRaises(TenantSuspendedError):
            service.authorize_access_request(token)

    # spec: F61.ac3 — a deactivated user is refused on the next request even with a live,
    # unexpired access token (membership recheck is per request, as in CS-061a, kept here).
    def test_deactivated_user_refused_per_request(self) -> None:
        from auth.service import AuthenticationError, TenantAuthService

        service = TenantAuthService.for_contract_fixture()
        token = service.login_json(**LOGIN).access_token
        service.set_user_active(False)
        with self.assertRaises(AuthenticationError):
            service.authorize_access_request(token)


class F61RevocationListTests(unittest.TestCase):
    # spec: F61.ac3 — a single token revoked by jti (single-session logout) is refused,
    # while a sibling token for the same user is unaffected.
    def test_single_token_revocation_by_jti(self) -> None:
        from auth.service import SessionRevokedError, TenantAuthService

        service = TenantAuthService.for_contract_fixture()
        first = service.login_json(**LOGIN).access_token
        second = service.login_json(**LOGIN).access_token
        service.revoke_token(_claims(first)["jti"])
        with self.assertRaises(SessionRevokedError):
            service.authorize_access_request(first)
        # The other live session is not collateral damage.
        self.assertEqual(FIXTURE_TENANT, service.authorize_access_request(second).tenant_id)

    # spec: F61.ac3 — the per-principal cutoff revokes every token issued before it, so a
    # bulk "log out everywhere" / deactivation invalidates existing sessions at once.
    def test_session_cutoff_revokes_tokens_issued_before_it(self) -> None:
        from auth.service import SessionRevokedError, TenantAuthService

        service = TenantAuthService.for_contract_fixture()
        token = service.login_json(**LOGIN).access_token
        # Cut off strictly after this token's iat.
        service.revoke_user_sessions(FIXTURE_TENANT, FIXTURE_USER)
        with self.assertRaises(SessionRevokedError):
            service.authorize_access_request(token)
        # A session established after the cutoff authorizes normally.
        fresh = service.login_json(**LOGIN).access_token
        self.assertEqual(FIXTURE_TENANT, service.authorize_access_request(fresh).tenant_id)

    # spec: F61.ac3 — revoking a user's sessions also burns their refresh families, so a
    # stolen refresh token cannot mint a fresh access token after deactivation/logout.
    def test_session_revocation_burns_refresh_families(self) -> None:
        from auth.service import AuthenticationError, TenantAuthService

        service = TenantAuthService.for_contract_fixture()
        issued = service.login_json(**LOGIN)
        service.revoke_user_sessions(FIXTURE_TENANT, FIXTURE_USER)
        with self.assertRaises(AuthenticationError):
            service.refresh_json(refresh_token=issued.refresh_token)

    # spec: F61.ac3 — revocation is fail-closed on malformed input: a non-UUID / empty jti
    # and an absent cutoff never silently authorize a token.
    def test_revocation_checks_fail_closed_on_malformed_input(self) -> None:
        from auth.service import AuthenticationError, TenantAuthService

        service = TenantAuthService.for_contract_fixture()
        for bad in ("", "not-a-uuid", "x" * 4097):
            with self.assertRaises(AuthenticationError, msg=repr(bad)):
                service.authorize_access_request(bad)


if __name__ == "__main__":
    unittest.main()
