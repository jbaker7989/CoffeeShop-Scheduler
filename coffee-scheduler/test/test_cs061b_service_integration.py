"""CS-061b disposable-PostgreSQL proof at the TenantAuthService layer (not just repo methods).

Opt-in: set ``CS061B_DATABASE_INTEGRATION=1`` and ``DATABASE_URL`` to an owner URL with the
0001..0011 chain applied. Each assertion builds a service over a FRESH auth_svc connection,
so durable, cross-instance behaviour is what is exercised — revocation, session cutoffs,
reset consume + session invalidation, throttle lockout, and encrypted MFA/replay/recovery.

The final test drives the service over the PRODUCTION ``PooledAuthRepository`` to prove the
BUG-038 gap (suspension not enforced because the service dispatches on
``isinstance(..., DatabaseAuthRepository)``). It is RED until BUG-038 is fixed.
"""
from __future__ import annotations

import os
import secrets
import sys
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import psycopg
except Exception:  # pragma: no cover - psycopg absent
    psycopg = None

SCHEDULER_ROOT = Path(__file__).resolve().parents[1]
if str(SCHEDULER_ROOT) not in sys.path:
    sys.path.insert(0, str(SCHEDULER_ROOT))

MFA_ENV = {"AUTH_MFA_ENC_KEY": "k" * 44}
PASSWORD = "correct horse battery staple"


def _now() -> datetime:
    return datetime.now(timezone.utc)


@unittest.skipUnless(os.environ.get("CS061B_DATABASE_INTEGRATION") and psycopg, "set CS061B_DATABASE_INTEGRATION=1")
class CS061bServiceDatabaseIntegration(unittest.TestCase):
    def setUp(self) -> None:
        from auth.passwords import Argon2idPasswordService

        self.owner = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")
        self.tid = str(uuid.uuid4())
        self.uid = str(uuid.uuid4())
        self.email = "owner@service.test"
        self.slug = "svc-" + self.tid
        with psycopg.connect(self.owner, autocommit=True) as c:
            c.execute("DROP ROLE IF EXISTS cs061b_svc_login")
            c.execute("CREATE ROLE cs061b_svc_login LOGIN PASSWORD 'svc'")
            c.execute("GRANT auth_svc TO cs061b_svc_login")
            c.execute(
                "INSERT INTO tenants(id,slug,name,status,plan,jurisdiction) VALUES(%s,%s,'svc','active','starter','NY')",
                (self.tid, self.slug),
            )
            c.execute(
                "INSERT INTO tenant_users(id,tenant_id,email,password_hash,role) VALUES(%s,%s,%s,%s,'owner')",
                (self.uid, self.tid, self.email, Argon2idPasswordService().hash(PASSWORD)),
            )
        self.auth_url = (
            self.owner.replace("postgresql://postgres:postgres@", "postgresql://cs061b_svc_login:svc@")
        )

    def tearDown(self) -> None:
        with psycopg.connect(self.owner, autocommit=True) as c:
            c.execute("DELETE FROM tenants WHERE id=%s", (self.tid,))
            c.execute("DROP ROLE IF EXISTS cs061b_svc_login")

    def _service(self, *, mfa=False):
        from auth.passwords import Argon2idPasswordService
        from auth.repository import DatabaseAuthRepository
        from auth.service import TenantAuthService, _build_contract_token_service
        from auth.mfa import MfaSecretCipher
        from auth.tokens import OpaqueRefreshTokenService

        # A fresh connection per service: proves cross-instance durability, not process state.
        # autocommit=True mirrors the production pooled auth connection (PooledAuthRepository),
        # where every auth_* call is its own atomic statement and nothing lingers in a txn.
        conn = psycopg.connect(self.auth_url, autocommit=True)
        self._conns.append(conn)
        return TenantAuthService(
            repository=DatabaseAuthRepository(conn),
            password_service=Argon2idPasswordService(),
            token_service=self._token_service,
            refresh_token_service=OpaqueRefreshTokenService(pepper=self._pepper),
            reset_service=__import__("auth.reset", fromlist=["PasswordResetService"]).PasswordResetService("r" * 32),
            mfa_cipher=MfaSecretCipher.from_environment(MFA_ENV) if mfa else None,
        )

    def _begin(self):
        # Shared token keyring + refresh pepper so tokens/digests are comparable across the
        # fresh per-call service connections.
        from auth.service import _build_contract_token_service

        self._conns = []
        self._token_service = _build_contract_token_service()
        self._pepper = secrets.token_urlsafe(32)

    def _suspend(self) -> None:
        with psycopg.connect(self.owner, autocommit=True) as c:
            c.execute("UPDATE tenants SET status='suspended' WHERE id=%s", (self.tid,))

    # spec: F61.ac3 — a fresh service over the DB enforces tenant suspension per request.
    def test_db_service_enforces_suspension(self) -> None:
        from auth.service import TenantSuspendedError

        self._begin()
        token = self._service().login_json(tenant_slug=self.slug, email=self.email, password=PASSWORD).access_token
        self._suspend()
        with self.assertRaises(TenantSuspendedError):
            self._service().authorize_access_request(token)
        for c in self._conns:
            c.close()

    # spec: F61.ac3 — revoke_user_sessions is durable: a fresh service rejects the old access
    # token (cutoff) and the old refresh token (burned family).
    def test_db_service_session_revocation_is_durable(self) -> None:
        from auth.service import AuthenticationError, SessionRevokedError

        self._begin()
        issuer = self._service()
        issued = issuer.login_json(tenant_slug=self.slug, email=self.email, password=PASSWORD)
        self._service().revoke_user_sessions(self.tid, self.uid)
        with self.assertRaises(SessionRevokedError):
            self._service().authorize_access_request(issued.access_token)
        with self.assertRaises(AuthenticationError):
            self._service().refresh_json(refresh_token=issued.refresh_token)
        for c in self._conns:
            c.close()

    # spec: F61.ac4 — password reset consume is durable and invalidates sessions across a
    # fresh instance: new password works, old fails, pre-reset access token is revoked.
    def test_db_service_reset_consume_and_session_invalidation(self) -> None:
        from auth.service import AuthenticationError, SessionRevokedError

        self._begin()
        issued = self._service().login_json(tenant_slug=self.slug, email=self.email, password=PASSWORD)
        req = self._service()
        req.request_password_reset(tenant_slug=self.slug, email=self.email)
        secret = req.sent_reset_secrets[-1]
        self._service().redeem_password_reset(secret=secret, new_password="new-durable-passphrase-x")
        # New credential works on a fresh instance; the old one does not.
        self._service().login_json(tenant_slug=self.slug, email=self.email, password="new-durable-passphrase-x")
        with self.assertRaises(AuthenticationError):
            self._service().login_json(tenant_slug=self.slug, email=self.email, password=PASSWORD)
        with self.assertRaises(SessionRevokedError):
            self._service().authorize_access_request(issued.access_token)
        # Reused reset secret is refused.
        from auth.reset import PasswordResetError
        with self.assertRaises(PasswordResetError):
            self._service().redeem_password_reset(secret=secret, new_password="another-passphrase-y")
        for c in self._conns:
            c.close()

    # spec: F61.ac4 — login throttle lockout is durable across fresh instances.
    def test_db_service_throttle_lockout_is_durable(self) -> None:
        from auth.ratelimit import AccountLockedError
        from auth.service import AuthenticationError

        self._begin()
        for _ in range(5):
            with self.assertRaises(AuthenticationError):
                self._service().login_json(
                    tenant_slug=self.slug, email=self.email, password="wrong", client_ip="203.0.113.5"
                )
        with self.assertRaises(AccountLockedError):
            self._service().login_json(
                tenant_slug=self.slug, email=self.email, password=PASSWORD, client_ip="203.0.113.5"
            )
        for c in self._conns:
            c.close()

    # spec: F61.ac4 — MFA enrol is durable; a fresh instance requires a TOTP code and rejects
    # replay of the consumed step; password alone never issues tokens.
    def test_db_service_mfa_enrol_require_and_replay(self) -> None:
        from auth.mfa import MfaRequiredError, TOTPService
        from auth.service import AuthenticationError

        self._begin()
        enrol_service = self._service(mfa=True)
        enrollment = enrol_service.begin_mfa_enrollment(tenant_id=self.tid, user_id=self.uid)
        now = datetime(2026, 11, 16, 12, 0, 0, tzinfo=timezone.utc)
        enrol_service.confirm_mfa_enrollment(
            tenant_id=self.tid, user_id=self.uid, code=TOTPService().code_at(enrollment.secret, now=now), now=now
        )
        # A fresh instance sees MFA is enabled and demands a code.
        with self.assertRaises(MfaRequiredError):
            self._service(mfa=True).login_json(
                tenant_slug=self.slug, email=self.email, password=PASSWORD, now=now
            )
        login_time = now + timedelta(seconds=60)
        code = TOTPService().code_at(enrollment.secret, now=login_time)
        issued = self._service(mfa=True).login_json(
            tenant_slug=self.slug, email=self.email, password=PASSWORD, mfa_code=code, now=login_time
        )
        self.assertTrue(issued.access_token)
        # Replaying the same code/step on a fresh instance is refused.
        with self.assertRaises(AuthenticationError):
            self._service(mfa=True).login_json(
                tenant_slug=self.slug, email=self.email, password=PASSWORD, mfa_code=code, now=login_time
            )
        for c in self._conns:
            c.close()

    # spec: F61.ac3 — BUG-038: the PRODUCTION PooledAuthRepository path must also enforce
    # suspension. RED until the service stops gating CS-061b on a concrete repo class.
    def test_production_pooled_repository_enforces_suspension(self) -> None:
        from auth.passwords import Argon2idPasswordService
        from auth.service import TenantAuthService, TenantSuspendedError, _build_contract_token_service
        from auth.tokens import OpaqueRefreshTokenService
        from api.auth_gateway import PooledAuthRepository
        from psycopg_pool import ConnectionPool

        self._begin()
        pool = ConnectionPool(conninfo=self.auth_url, kwargs={"prepare_threshold": None, "autocommit": True}, min_size=1, open=True)
        try:
            service = TenantAuthService(
                repository=PooledAuthRepository(pool),
                password_service=Argon2idPasswordService(),
                token_service=self._token_service,
                refresh_token_service=OpaqueRefreshTokenService(pepper=self._pepper),
            )
            token = service.login_json(tenant_slug=self.slug, email=self.email, password=PASSWORD).access_token
            self._suspend()
            with self.assertRaises(TenantSuspendedError):
                service.authorize_access_request(token)
        finally:
            pool.close()
            for c in self._conns:
                c.close()

    def test_pooled_malformed_mfa_cipher_is_opaque_auth_failure(self) -> None:
        from auth.passwords import Argon2idPasswordService
        from auth.service import TenantAuthService, AuthenticationError
        from auth.tokens import OpaqueRefreshTokenService
        from auth.mfa import MfaSecretCipher, TOTPService
        from api.auth_gateway import PooledAuthRepository
        from psycopg_pool import ConnectionPool

        self._begin()
        pool = ConnectionPool(conninfo=self.auth_url, kwargs={"prepare_threshold": None, "autocommit": True}, min_size=1, open=True)
        try:
            service = TenantAuthService(repository=PooledAuthRepository(pool), password_service=Argon2idPasswordService(), token_service=self._token_service, refresh_token_service=OpaqueRefreshTokenService(pepper=self._pepper), mfa_cipher=MfaSecretCipher.from_environment(MFA_ENV))
            enrollment=service.begin_mfa_enrollment(tenant_id=self.tid,user_id=self.uid)
            now=datetime(2026,11,16,12,0,0,tzinfo=timezone.utc)
            service.confirm_mfa_enrollment(tenant_id=self.tid,user_id=self.uid,code=TOTPService().code_at(enrollment.secret,now),now=now)
            with psycopg.connect(self.owner, autocommit=True) as c:
                c.execute("UPDATE tenant_users SET mfa_secret_ciphertext=%s WHERE tenant_id=%s AND id=%s",(b'M1'+b'\\x00'*40,self.tid,self.uid))
            with self.assertRaises(AuthenticationError):
                service.login_json(tenant_slug=self.slug,email=self.email,password=PASSWORD,mfa_code='000000',now=now+timedelta(seconds=30))
        finally:
            pool.close()
            for c in self._conns: c.close()


if __name__ == "__main__":
    unittest.main()
