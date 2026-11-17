"""CS-061b regression: hardening must work over the PRODUCTION repository, not only the
in-memory fixture or a direct DatabaseAuthRepository (BUG-038).

The gateway builds the service over ``api.auth_gateway.PooledAuthRepository``, which is an
``AuthRepository`` but *not* a ``DatabaseAuthRepository`` and *not* an
``InMemoryAuthRepository``. ``TenantAuthService`` routes every CS-061b behaviour through
``isinstance(self._repo, DatabaseAuthRepository)`` / ``InMemoryAuthRepository`` branches, so
over any other ``AuthRepository`` it silently skips the DB-backed path: tenant suspension is
never enforced and password-reset redeem raises ``AttributeError`` on ``self._repo._users``.

These tests use a minimal non-fixture ``AuthRepository`` (the same category as the production
pooled repository) and require no database. They are RED against the current candidate and
pin the fix: the service must drive CS-061b state through the repository interface, not a
concrete-class check.
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path


SCHEDULER_ROOT = Path(__file__).resolve().parents[1]
if str(SCHEDULER_ROOT) not in sys.path:
    sys.path.insert(0, str(SCHEDULER_ROOT))

TENANT = "00000000-0000-0000-0000-0000000000aa"
USER = "00000000-0000-0000-0000-0000000000bb"


def _build_service(repo):
    import secrets
    from auth.passwords import Argon2idPasswordService
    from auth.service import TenantAuthService, _build_contract_token_service
    from auth.tokens import OpaqueRefreshTokenService

    return TenantAuthService(
        repository=repo,
        password_service=Argon2idPasswordService(),
        token_service=_build_contract_token_service(),
        refresh_token_service=OpaqueRefreshTokenService(pepper=secrets.token_urlsafe(32)),
    )


def _make_repo(*, tenant_active: bool):
    """A production-shaped AuthRepository (neither the in-memory fixture nor
    DatabaseAuthRepository) that reports its own CS-061b state and records calls."""
    from auth.repository import AuthRepository, TenantMembershipRecord, TenantUserRecord

    class _NonFixtureRepo(AuthRepository):
        def __init__(self) -> None:
            self.calls: list[str] = []
            self._pw = __import__("auth.passwords", fromlist=["Argon2idPasswordService"]).Argon2idPasswordService()
            self._hash = self._pw.hash("correct horse battery staple")

        # --- CS-061a identity surface (so authenticate_access_token reconciles) ---
        def get_tenant_user_by_email(self, tenant_slug, email):
            return TenantUserRecord(
                id=USER, tenant_id=TENANT, tenant_slug=tenant_slug, email=email,
                password_hash=self._hash, role="owner", is_active=True,
            )

        def get_tenant_user_by_id(self, tenant_id, user_id):
            if tenant_id == TENANT and user_id == USER:
                return TenantMembershipRecord(id=USER, tenant_id=TENANT, role="owner", is_active=True)
            return None

        def record_login(self, user_id, when=None): self.calls.append("record_login")
        def insert_refresh_token(self, record): self.calls.append("insert_refresh_token")
        def get_refresh_token(self, token_digest): return None
        def consume_and_replace(self, *, presented_digest, child, now=None): raise NotImplementedError
        def revoke_family(self, family_id, when=None): self.calls.append("revoke_family"); return 0

        # --- CS-061b surface the service must actually use ---
        def access_state(self, tenant_id, user_id, jti, issued_at):
            self.calls.append("access_state")
            return {"role": "owner", "is_active": True, "tenant_active": tenant_active, "revoked": False}

        def revoke_user_sessions(self, tenant_id, user_id, when): self.calls.append("revoke_user_sessions")
        def issue_password_reset(self, *a): self.calls.append("issue_password_reset")
        def consume_password_reset(self, *a): self.calls.append("consume_password_reset"); return (TENANT, USER)
        def record_login_attempt(self, *a): self.calls.append("record_login_attempt"); return False
        def login_locked(self, *a): self.calls.append("login_locked"); return False

    return _NonFixtureRepo()


def _mint(service) -> str:
    now = datetime.now(timezone.utc)
    return service._tokens.issue_access_token(sub=USER, tenant_id=TENANT, user_id=USER, role="owner", now=now)


class CS061bProductionRepoPathTests(unittest.TestCase):
    # spec: F61.ac3 — over a production (non-fixture) repository that reports the tenant
    # suspended, authorize_access_request MUST refuse with TenantSuspendedError.
    def test_suspension_enforced_over_production_repository(self) -> None:
        from auth.service import TenantSuspendedError

        repo = _make_repo(tenant_active=False)
        service = _build_service(repo)
        token = _mint(service)
        with self.assertRaises(TenantSuspendedError):
            service.authorize_access_request(token)

    # spec: F61.ac3 — the active-tenant case consults the repository's per-request state
    # (so suspension/deactivation can take effect), i.e. access_state is actually called.
    def test_authorize_consults_repository_state(self) -> None:
        repo = _make_repo(tenant_active=True)
        service = _build_service(repo)
        token = _mint(service)
        service.authorize_access_request(token)
        self.assertIn("access_state", repo.calls, "service must query the repo's per-request access state")

    # spec: F61.ac4 — password-reset redeem over a production repository must route through
    # the repository (consume_password_reset), never crash on an in-memory-only attribute.
    def test_password_reset_redeem_uses_repository(self) -> None:
        repo = _make_repo(tenant_active=True)
        service = _build_service(repo)
        service.request_password_reset(tenant_slug="probe", email="p@example.test")
        secret = service.sent_reset_secrets[-1]
        service.redeem_password_reset(secret=secret, new_password="a-sufficiently-long-new-pass")
        self.assertIn("consume_password_reset", repo.calls)


if __name__ == "__main__":
    unittest.main()
