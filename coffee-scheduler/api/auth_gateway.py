"""Production wiring that makes the CS-061a auth boundary reachable from the gateway.

BUG-035: the CS-061a ``auth.api.create_auth_router`` and its JWKS were implemented but never
mounted, so the reachable app still served the legacy CS-030 demo ``/auth/login``. This module
builds a real :class:`~auth.service.TenantAuthService` from the environment, fail-closed, and
supplies the small HTTP helpers the gateway needs to authenticate protected requests from
*reconciled* CS-061a claims (never from request-supplied tenant input).

Design:

* **Fail closed at startup.** Missing/invalid auth DSN, JWT keyring, issuer/audience, or refresh
  pepper raises here. There is no fallback to ``SESSION_SECRET`` or the retired ``DEMO_MANAGER_*``
  demo credentials — the demo login is not mounted in production.
* **Least-privilege, pool-safe storage.** Credential/refresh access runs over a dedicated
  ``AUTH_DATABASE_URL`` connection pool whose login principal is a member of the NOLOGIN
  ``auth_svc`` role (``GRANT auth_svc TO <login>``) and therefore inherits EXECUTE on exactly the
  ``auth_*`` SECURITY DEFINER functions — nothing else. Each repository call borrows its own
  pooled connection, so concurrent gateway requests never share a psycopg connection.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Mapping

from fastapi import Request

from auth.jwt import RS256KeyRing, RS256TokenService
from auth.passwords import Argon2idPasswordService
from auth.repository import (
    AuthRepository,
    AcceptedInvite,
    ConsumeResult,
    DatabaseAuthRepository,
    RefreshTokenRecord,
    TenantMembershipRecord,
    TenantUserRecord,
    InvitationRecord,
    AuditEntry,
)
from auth.service import (
    AUTH_DATABASE_URL_ENV,
    AUTH_JWT_AUDIENCE_ENV,
    AUTH_JWT_ISSUER_ENV,
    TenantAuthService,
    _positive_env_int,
)
from auth.tokens import OpaqueRefreshTokenService
from auth.reset import PasswordResetService
from auth.mfa import MfaSecretCipher


_AUTH_CREDENTIAL_TABLES = ("tenant_users", "auth_refresh_tokens")


class GatewayAuthConfigError(RuntimeError):
    """The CS-061a auth boundary could not be wired from the environment. Fail closed."""


class PooledAuthRepository(AuthRepository):
    """A connection-per-call :class:`AuthRepository` backed by a psycopg pool.

    Each method borrows one autocommit connection from ``AUTH_DATABASE_URL`` and delegates to a
    transient :class:`~auth.repository.DatabaseAuthRepository`, so a shared connection is never
    used by two concurrent requests. Every auth DB operation is a single ``auth_*`` function call
    (``consume_and_replace`` is one atomic SECURITY DEFINER call), so autocommit is correct — the
    rotation/replay atomicity lives inside the function, not in a client-side transaction.
    """

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    def _repo(self, conn) -> DatabaseAuthRepository:
        return DatabaseAuthRepository(conn)

    def _call(self, operation: str, *args: Any, **kwargs: Any) -> Any:
        if self._pool.closed:
            self._pool.open()
        with self._pool.connection() as conn:
            return getattr(self._repo(conn), operation)(*args, **kwargs)

    def get_tenant_user_by_email(self, tenant_slug: str, email: str) -> TenantUserRecord | None:
        return self._call("get_tenant_user_by_email", tenant_slug, email)

    def get_tenant_user_by_id(self, tenant_id: str, user_id: str) -> TenantMembershipRecord | None:
        return self._call("get_tenant_user_by_id", tenant_id, user_id)

    def record_login(self, user_id: str, when: datetime | None = None) -> None:
        return self._call("record_login", user_id, when)

    def insert_refresh_token(self, record: RefreshTokenRecord) -> None:
        return self._call("insert_refresh_token", record)

    def get_refresh_token(self, token_digest: str) -> RefreshTokenRecord | None:
        return self._call("get_refresh_token", token_digest)

    def consume_and_replace(
        self, *, presented_digest: str, child: RefreshTokenRecord, now: datetime | None = None
    ) -> ConsumeResult:
        return self._call("consume_and_replace", presented_digest=presented_digest, child=child, now=now)

    def revoke_family(self, family_id: str, when: datetime | None = None) -> int:
        return self._call("revoke_family", family_id, when)

    def access_state(self, tenant_id: str, user_id: str, jti: str, issued_at: datetime) -> dict | None:
        return self._call("access_state", tenant_id, user_id, jti, issued_at)

    def revoke_access_token(self, jti: str, tenant_id: str, user_id: str, expires_at: datetime) -> None:
        return self._call("revoke_access_token", jti, tenant_id, user_id, expires_at)

    def revoke_user_sessions(self, tenant_id: str, user_id: str, when: datetime) -> None:
        return self._call("revoke_user_sessions", tenant_id, user_id, when)

    def issue_password_reset(self, tenant_id: str, user_id: str, digest: str, expires_at: datetime) -> None:
        return self._call("issue_password_reset", tenant_id, user_id, digest, expires_at)

    def consume_password_reset(self, digest: str, password_hash: str, when: datetime) -> tuple[str, str] | None:
        return self._call("consume_password_reset", digest, password_hash, when)

    def record_login_attempt(self, tenant_id: str, email: str, ip: str, succeeded: bool, when: datetime, max_failures: int = 5, window_seconds: int = 900) -> bool:
        return self._call("record_login_attempt", tenant_id, email, ip, succeeded, when, max_failures, window_seconds)

    def login_locked(self, tenant_id: str, email: str, ip: str, when: datetime, max_failures: int = 5, window_seconds: int = 900) -> bool:
        return self._call("login_locked", tenant_id, email, ip, when, max_failures, window_seconds)

    def get_mfa(self, tenant_id: str, user_id: str) -> dict | None:
        return self._call("get_mfa", tenant_id, user_id)

    def store_mfa(self, tenant_id: str, user_id: str, ciphertext: bytes, step: int, digests: list[str]) -> None:
        return self._call("store_mfa", tenant_id, user_id, ciphertext, step, digests)

    def consume_mfa_step(self, tenant_id: str, user_id: str, step: int) -> bool:
        return self._call("consume_mfa_step", tenant_id, user_id, step)

    def consume_recovery_code(self, tenant_id: str, user_id: str, digest: str) -> bool:
        return self._call("consume_recovery_code", tenant_id, user_id, digest)

    def create_invitation(self, invitation: InvitationRecord) -> str:
        return self._call("create_invitation", invitation)

    def get_invitation(self, token_digest: str) -> InvitationRecord | None:
        return self._call("get_invitation", token_digest)

    def accept_invitation(self, token_digest: str, password_hash: str, when: datetime) -> AcceptedInvite | None:
        return self._call("accept_invitation", token_digest, password_hash, when)

    def list_invitations(self, tenant_id: str) -> list[InvitationRecord]:
        return self._call("list_invitations", tenant_id)

    def revoke_invitation(self, tenant_id: str, invitation_id: str) -> bool:
        return self._call("revoke_invitation", tenant_id, invitation_id)

    def record_audit(self, entry: AuditEntry) -> int:
        return self._call("record_audit", entry)


def _auth_pool(dsn: str) -> Any:
    # PgBouncer transaction pooling-safe (prepare_threshold=None); autocommit because every auth
    # operation is a single atomic auth_* function call. Opened lazily so import fails closed on
    # missing CONFIG without requiring the database to be reachable at import time. psycopg_pool is
    # imported here (not at module top) so the auth boundary and its ASGI tests load without it.
    from psycopg_pool import ConnectionPool

    return ConnectionPool(
        conninfo=dsn,
        kwargs={"prepare_threshold": None, "autocommit": True},
        min_size=1,
        open=False,
    )


def _validate_auth_database_principal(pool: Any) -> None:
    """Prove the configured auth connection is the constrained auth-service port.

    The URL alone cannot reliably establish who it authenticates as: role names may be
    URL-encoded and production login principals are deployment-specific.  Verify the
    *connected* principal before exposing a gateway: it must not be a superuser or
    ``BYPASSRLS`` role, must inherit the migration-owned ``auth_svc`` capability, and
    must not have direct access to the credential tables.  This catches a mistakenly
    supplied migrator/superuser URL instead of allowing it to serve login traffic.
    """
    if pool.closed:
        pool.open(wait=True)
    try:
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT r.rolsuper, r.rolbypassrls, "
                    "pg_has_role(current_user, 'auth_svc', 'MEMBER') "
                    "FROM pg_roles AS r WHERE r.rolname = current_user"
                )
                row = cur.fetchone()
                if row is None:
                    raise GatewayAuthConfigError("authenticated database role could not be inspected")
                is_superuser, bypasses_rls, has_auth_service_membership = row
                if is_superuser or bypasses_rls or not has_auth_service_membership:
                    raise GatewayAuthConfigError(
                        "AUTH_DATABASE_URL must authenticate as a non-superuser, non-BYPASSRLS auth_svc member"
                    )
                for table in _AUTH_CREDENTIAL_TABLES:
                    cur.execute(
                        "SELECT has_table_privilege(current_user, %s, 'SELECT, INSERT, UPDATE, DELETE')",
                        (table,),
                    )
                    if cur.fetchone()[0]:
                        raise GatewayAuthConfigError(
                            "AUTH_DATABASE_URL principal must use auth_* functions, not direct credential-table privileges"
                        )
    except GatewayAuthConfigError:
        raise
    except Exception as exc:
        raise GatewayAuthConfigError("AUTH_DATABASE_URL principal validation failed") from exc


def build_auth_service_from_env(
    env: Mapping[str, str] | None = None,
    *,
    now: Callable[[], datetime] | None = None,
) -> TenantAuthService:
    """Build the production CS-061a auth service, fail-closed. Raises :class:`GatewayAuthConfigError`
    when any required auth configuration is absent or invalid — never degrades or falls back."""
    import os

    source = os.environ if env is None else env
    dsn = source.get(AUTH_DATABASE_URL_ENV)
    if not dsn:
        raise GatewayAuthConfigError(f"{AUTH_DATABASE_URL_ENV} is required for the CS-061a auth boundary")
    for forbidden in ("app_rw", "app_ro"):
        if f"://{forbidden}" in dsn or f"@{forbidden}" in dsn:
            raise GatewayAuthConfigError(
                f"{AUTH_DATABASE_URL_ENV} must not use the {forbidden} RLS role (use the auth_svc login principal)"
            )
    issuer = source.get(AUTH_JWT_ISSUER_ENV)
    audience = source.get(AUTH_JWT_AUDIENCE_ENV)
    if not issuer or not audience:
        raise GatewayAuthConfigError(f"{AUTH_JWT_ISSUER_ENV} and {AUTH_JWT_AUDIENCE_ENV} are required")
    try:
        key_ring = RS256KeyRing.from_environment(source)
        token_service = RS256TokenService(key_ring, issuer=issuer, audience=audience)
        refresh_service = OpaqueRefreshTokenService.from_environment(source)
        reset_service = PasswordResetService(source.get("AUTH_PASSWORD_RESET_PEPPER", ""), now or datetime.now, _positive_env_int(source, "AUTH_PASSWORD_RESET_TTL_SECONDS", 3600))
        mfa_cipher = MfaSecretCipher.from_environment(source)
    except Exception as exc:  # JWTConfigurationError / ValueError — never echo key material
        raise GatewayAuthConfigError(f"CS-061a auth configuration is invalid: {exc}") from exc

    pool = _auth_pool(dsn)
    try:
        _validate_auth_database_principal(pool)
    except Exception:
        pool.close()
        raise

    kwargs = {} if now is None else {"now": now}
    return TenantAuthService(
        repository=PooledAuthRepository(pool),
        password_service=Argon2idPasswordService(),
        token_service=token_service,
        refresh_token_service=refresh_service,
        reset_service=reset_service,
        mfa_cipher=mfa_cipher,
        throttle_max_failures=_positive_env_int(source, "AUTH_LOGIN_MAX_FAILURES", 5),
        throttle_window_seconds=_positive_env_int(source, "AUTH_LOGIN_LOCKOUT_SECONDS", 900),
        invite_ttl_seconds=_positive_env_int(source, "INVITE_TTL", 72 * 60 * 60),
        **kwargs,
    )


# Cookie name the browser transport uses for the short-lived access credential (Path=/), so the
# gateway can read it on protected requests. Mirrors auth.api.BrowserCookieConfig.access_name.
ACCESS_COOKIE_NAME = "access_token"


def _cookie_pair_count(request: Request, name: str) -> int:
    """Count an exact cookie name across the raw Cookie headers (FastAPI collapses duplicates)."""
    count = 0
    for header in request.headers.getlist("cookie"):
        for pair in header.split(";"):
            cookie_name, separator, _ = pair.strip().partition("=")
            if separator and cookie_name == name:
                count += 1
    return count


def extract_access_credential(request: Request) -> str | None:
    """Return the single access token presented, or ``None`` if missing, malformed, or ambiguous.

    Exactly one source is accepted: an ``Authorization: Bearer`` header OR one ``access_token``
    cookie — never both, never a duplicate. An ambiguous credential is treated as no credential
    (fail closed) so a caller cannot smuggle a second token past validation.
    """
    auth_headers = request.headers.getlist("authorization")
    has_bearer = len(auth_headers) >= 1
    cookie_count = _cookie_pair_count(request, ACCESS_COOKIE_NAME)

    if has_bearer and cookie_count >= 1:
        return None  # ambiguous: two transports at once
    if len(auth_headers) > 1:
        return None  # multiple Authorization headers
    if has_bearer:
        scheme, separator, token = auth_headers[0].partition(" ")
        if not separator or scheme.lower() != "bearer":
            return None
        token = token.strip()
        return token or None
    if cookie_count == 1:
        return request.cookies.get(ACCESS_COOKIE_NAME) or None
    return None  # zero or duplicate cookies
