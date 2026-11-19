"""Tenant authentication service for CS-061a (F61 AC2).

This is the policy layer: it composes the Argon2id password service (``auth.passwords``),
the refresh-family persistence (``auth.repository``), and the RS256 token services
(``auth.jwt`` / ``auth.tokens``) into the four login/refresh flows. Security decisions
live here, never in the route handlers (``auth.api``):

* Login succeeds only for an **active** tenant user with a matching Argon2id password;
  an unknown email, an inactive user, and a wrong password all raise the *same*
  :class:`AuthenticationError` so the failure is externally indistinguishable.
* Access-token validation is followed by **canonical membership reconciliation** — the
  identity is re-loaded by ``(tenant_id, user_id)`` and its stored tenant/role must agree
  with the token before it is trusted. A validly signed token cannot select another
  tenant, user, or role. This is the strict tenant-claim-authority rule.
* Refresh rotation/replay *authority* is delegated to the repository's atomic
  ``consume_and_replace``; this layer only maps its outcome to the right exception.
"""
from __future__ import annotations

import os
import base64
import json
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Callable, Mapping, Protocol
import logging

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from .jwt import (
    ACCESS_TOKEN_TTL_SECONDS,
    JWTConfigurationError,
    RS256KeyRing,
    RS256TokenService,
    TokenValidationError,
)
from .passwords import Argon2idPasswordService, InvalidPasswordHash
from .repository import (
    AuthRepository,
    InMemoryAuthRepository,
    ConsumeStatus,
    RefreshTokenRecord,
    InvitationRecord, AuditEntry,
)
from .tokens import MAX_REFRESH_TOKEN_BYTES, OpaqueRefreshTokenService
from .ratelimit import LoginAttemptKey
from .reset import PasswordResetService, PasswordResetError
from .mfa import TOTPService, MfaSecretCipher, RecoveryCodeService, MfaRequiredError, MfaEnrollmentError, MfaReplayError

# Environment variable names for the production auth wiring. The auth service connects on
# its OWN dsn as the dedicated auth_svc role — never the data-access app_rw/app_ro roles.
AUTH_DATABASE_URL_ENV = "AUTH_DATABASE_URL"
AUTH_JWT_ISSUER_ENV = "AUTH_JWT_ISSUER"
AUTH_JWT_AUDIENCE_ENV = "AUTH_JWT_AUDIENCE"

# One opaque message for every credential/refresh failure: callers must not be able to
# distinguish "no such user" from "wrong password" from "inactive user".
_GENERIC_AUTH_FAILURE = "authentication failed"

def _positive_env_int(source: Mapping[str, str], name: str, default: int) -> int:
    value = source.get(name, str(default))
    try: parsed = int(value)
    except (TypeError, ValueError): raise ValueError(f"{name} must be a positive integer") from None
    if parsed <= 0: raise ValueError(f"{name} must be a positive integer")
    return parsed


class AuthenticationError(Exception):
    """A login, refresh, or access-token identity could not be authenticated."""


class RefreshReplayError(AuthenticationError):
    """A refresh token was reused after rotation (or its family was burned by a replay).

    A subclass of :class:`AuthenticationError` because a replay is an authentication
    failure, but distinguishable so callers can alarm on token theft specifically.
    """
class TenantSuspendedError(AuthenticationError): pass
class SessionRevokedError(AuthenticationError): pass
class InvitationError(AuthenticationError): pass
class InvitationPermissionError(InvitationError): pass
class InvitationConflictError(InvitationError): pass

class InvitationNotifier(Protocol):
    def notify(self, *, email: str, accept_url: str) -> None: ...

class LogInvitationNotifier:
    def notify(self, *, email: str, accept_url: str) -> None:
        logging.getLogger(__name__).info("Invitation link for %s: %s", email, accept_url)


@dataclass(frozen=True)
class IssuedTokens:
    """The result of a login or refresh. ``refresh_token`` is the raw opaque secret; the
    browser transport puts it in a cookie and omits it from the response body."""

    access_token: str
    expires_in: int
    refresh_token: str
    token_type: str = "bearer"

@dataclass(frozen=True)
class InvitationIssued:
    invitation_id: str
    expires_at: datetime
    token: str


@dataclass(frozen=True)
class Principal:
    """A reconciled, trusted request identity. ``is_saas_admin`` is always False here —
    a SaaS admin never authenticates through the tenant flow (CS-061a boundary)."""

    tenant_id: str
    user_id: str
    role: str
    is_saas_admin: bool = False


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TenantAuthService:
    def __init__(
        self,
        *,
        repository: AuthRepository,
        password_service: Argon2idPasswordService,
        token_service: RS256TokenService,
        refresh_token_service: OpaqueRefreshTokenService,
        now: Callable[[], datetime] = _utcnow,
        reset_service: PasswordResetService | None = None,
        mfa_cipher: MfaSecretCipher | None = None,
        throttle_max_failures: int = 5,
        throttle_window_seconds: int = 900,
        invite_ttl_seconds: int = 72 * 60 * 60,
        invitation_notifier: InvitationNotifier | None = None,
    ) -> None:
        self._repo = repository
        self._passwords = password_service
        self._tokens = token_service
        self._refresh = refresh_token_service
        self._now = now
        self._reset = reset_service or PasswordResetService(secrets.token_urlsafe(32), now)
        self.sent_reset_secrets: list[str] = []
        self._mfa_cipher = mfa_cipher
        if throttle_max_failures <= 0 or throttle_window_seconds <= 0:
            raise ValueError("login throttle configuration must be positive")
        self._throttle_max_failures = throttle_max_failures
        self._throttle_window_seconds = throttle_window_seconds
        if invite_ttl_seconds <= 0: raise ValueError("INVITE_TTL must be a positive integer")
        self._invite_ttl_seconds = invite_ttl_seconds
        self._invitation_notifier = invitation_notifier or LogInvitationNotifier()
        self._pending_mfa: dict[tuple[str,str], str] = {}
        self.mfa_recovery_codes: list[str] = []
        # A valid Argon2id hash of a random secret, verified against when the email lookup
        # misses so an unknown email costs the same Argon2 work as a wrong password. Without
        # it, response time leaks whether an email is registered (tenant/email enumeration).
        self._dummy_password_hash = password_service.hash(secrets.token_urlsafe(32))

    # ----- login -----
    def login_json(self, *, tenant_slug: str, email: str, password: str, client_ip: str = "0.0.0.0", mfa_code: str | None = None, now: datetime | None = None) -> IssuedTokens:
        """JSON/bearer login: verify active credentials, issue an access + refresh pair."""
        user_hint = self._repo.get_tenant_user_by_email(tenant_slug, email)
        key = LoginAttemptKey(tenant_id=user_hint.tenant_id if user_hint else tenant_slug, email=email, ip=client_ip)
        if self._repo.login_locked(key.tenant_id,key.email,key.ip,now or self._now(),self._throttle_max_failures,self._throttle_window_seconds):
            from .ratelimit import AccountLockedError; raise AccountLockedError(_GENERIC_AUTH_FAILURE)
        try:
            user = self._authenticate_credentials(tenant_slug=tenant_slug, email=email, password=password)
        except AuthenticationError:
            self._repo.record_login_attempt(key.tenant_id,key.email,key.ip,False,now or self._now(),self._throttle_max_failures,self._throttle_window_seconds)
            self._audit(AuditEntry(user_hint.tenant_id if user_hint else None, user_hint.id if user_hint else None, "tenant_user" if user_hint else "system", "auth.login.failed", ip=client_ip))
            raise
        self._repo.record_login_attempt(key.tenant_id,key.email,key.ip,True,now or self._now(),self._throttle_max_failures,self._throttle_window_seconds)
        now = now or self._now()
        state=self._repo.get_mfa(user.tenant_id,user.id)
        if state:
            if not mfa_code: raise MfaRequiredError("authentication failed")
            try:
                if self._mfa_cipher is None: raise AuthenticationError(_GENERIC_AUTH_FAILURE)
                secret=self._mfa_cipher.decrypt(state['ciphertext']).decode()
                try: state['last']=TOTPService().consume(secret,mfa_code,now,last_timestep=state.get('last',state.get('last_timestep')))
                except MfaReplayError:
                    if not self._repo.consume_recovery_code(user.tenant_id,user.id,RecoveryCodeService().digest(mfa_code)): raise MfaReplayError('invalid')
                else:
                    if not self._repo.consume_mfa_step(user.tenant_id,user.id,state['last']): raise MfaReplayError('replayed')
            # AES-GCM authentication/key failures are deliberately indistinguishable from
            # a bad OTP.  Never leak corrupt ciphertext, key rotation, or MFA enrollment.
            except (MfaReplayError, ValueError, UnicodeDecodeError):
                raise AuthenticationError(_GENERIC_AUTH_FAILURE) from None
        self._repo.record_login(user.id, now)
        self._audit(AuditEntry(user.tenant_id, user.id, "tenant_user", "auth.login.succeeded", ip=client_ip))
        return self._issue(tenant_id=user.tenant_id, user_id=user.id, role=user.role, now=now)

    # The browser login shares identical credential semantics; the cookie/body transport
    # split is enforced in auth.api, keeping CSRF/token-exposure rules out of this layer.
    login_browser = login_json

    def _audit(self, entry: AuditEntry) -> None:
        try: self._repo.record_audit(entry)
        except Exception:
            logging.getLogger(__name__).exception("audit write failed for %s", entry.action)
            raise

    def create_invitation(self, principal: Principal, email: str, role: str, *, accept_url_base: str = "/invitations/accept") -> InvitationIssued:
        ranks = {"read_only": 0, "staff": 1, "manager": 2, "owner": 3}
        if principal.role not in ("owner", "manager") or role not in ranks or ranks[role] > ranks.get(principal.role, -1):
            raise InvitationPermissionError("forbidden")
        now = self._now(); token = secrets.token_urlsafe(32)
        record = InvitationRecord(str(uuid.uuid4()), principal.tenant_id, email, role, self._refresh.digest(token), principal.user_id, "pending", now + timedelta(seconds=self._invite_ttl_seconds), created_at=now)
        try: invitation_id = self._repo.create_invitation(record)
        except ValueError as exc: raise InvitationConflictError("invitation unavailable") from exc
        self._audit(AuditEntry(principal.tenant_id, principal.user_id, "tenant_user", "invitation.created", "invitation", invitation_id, after={"email": email, "role": role}))
        issued = InvitationIssued(invitation_id, record.expires_at, token)
        self._invitation_notifier.notify(email=email, accept_url=f"{accept_url_base}?token={token}")
        return issued

    def accept_invitation(self, token: str, password: str, display_name: str | None = None) -> IssuedTokens:
        if not isinstance(token, str) or not token or len(token) > 4096: raise InvitationError(_GENERIC_AUTH_FAILURE)
        try: password_hash = self._passwords.hash(password)
        except Exception as exc: raise InvitationError(_GENERIC_AUTH_FAILURE) from exc
        accepted = self._repo.accept_invitation(self._refresh.digest(token), password_hash, self._now())
        if accepted is None: raise InvitationError(_GENERIC_AUTH_FAILURE)
        self._audit(AuditEntry(accepted.tenant_id, accepted.user_id, "tenant_user", "invitation.accepted", "invitation", None, after={"display_name": display_name} if display_name else None))
        return self._issue(tenant_id=accepted.tenant_id, user_id=accepted.user_id, role=accepted.role, now=self._now())

    def list_invitations(self, principal: Principal):
        if principal.role not in ("owner", "manager"): raise InvitationPermissionError("forbidden")
        return self._repo.list_invitations(principal.tenant_id)

    def revoke_invitation(self, principal: Principal, invitation_id: str) -> None:
        if principal.role not in ("owner", "manager"): raise InvitationPermissionError("forbidden")
        if not self._repo.revoke_invitation(principal.tenant_id, invitation_id): raise InvitationError("not found")
        self._audit(AuditEntry(principal.tenant_id, principal.user_id, "tenant_user", "invitation.revoked", "invitation", invitation_id))

    def _authenticate_credentials(self, *, tenant_slug: str, email: str, password: str):
        user = self._repo.get_tenant_user_by_email(tenant_slug, email)
        if user is None:
            # Do the same Argon2 work a real verify would, then fail identically — the miss
            # must be indistinguishable from a wrong password in both result and timing.
            self._passwords.verify(password, self._dummy_password_hash)
            raise AuthenticationError(_GENERIC_AUTH_FAILURE)
        try:
            password_ok = self._passwords.verify(password, user.password_hash)
        except InvalidPasswordHash:
            # A stored credential that is not a valid Argon2id hash fails closed, with the
            # same opaque error — never a silent non-match that masks misconfiguration.
            raise AuthenticationError(_GENERIC_AUTH_FAILURE) from None
        if not password_ok or not user.is_active:
            raise AuthenticationError(_GENERIC_AUTH_FAILURE)
        return user

    # ----- refresh -----
    def refresh_json(self, *, refresh_token: str) -> IssuedTokens:
        """Atomically consume a refresh token and issue a replacement access + refresh pair
        in the same family. The repository derives the family/tenant/user from the locked
        parent and returns them on the rotated child, so the successor access token is
        minted from that canonical identity — never from a caller-supplied value, and with
        no pre-read of the refresh row. Replay revokes the whole family and raises."""
        if not isinstance(refresh_token, str) or not refresh_token or len(refresh_token) > MAX_REFRESH_TOKEN_BYTES:
            # Missing/oversized/wrong-typed input is rejected before any digest work.
            raise AuthenticationError(_GENERIC_AUTH_FAILURE)
        now = self._now()
        presented_digest = self._refresh.digest(refresh_token)
        minted = self._refresh.mint(now=now)
        # consume_and_replace derives the child's family_id/tenant_id/user_id from the locked
        # parent and ignores these three inputs; only id/token_digest/issued_at/expires_at are
        # honoured. The atomic op is the sole authority, so we never pre-read the parent row.
        child = RefreshTokenRecord(
            id=str(uuid.uuid4()),
            family_id="",
            tenant_id="",
            user_id="",
            token_digest=minted.digest,
            issued_at=now,
            expires_at=minted.expires_at,  # fixed 7 days from this rotation, no sliding reuse
        )
        result = self._repo.consume_and_replace(presented_digest=presented_digest, child=child, now=now)
        if result.replayed:
            self._audit(AuditEntry(None, None, "system", "auth.refresh.reuse_detected"))
            raise RefreshReplayError("refresh token reuse detected")
        if result.status is not ConsumeStatus.ROTATED:
            # EXPIRED / NOT_FOUND / any non-rotation outcome.
            raise AuthenticationError(_GENERIC_AUTH_FAILURE)

        # Trust the canonical family/tenant/user the repository assigned to the rotated child,
        # then re-check membership so a deactivated user cannot keep minting access tokens for
        # the remaining refresh life.
        rotated = result.child
        member = self._repo.get_tenant_user_by_id(rotated.tenant_id, rotated.user_id)
        if member is None or not member.is_active:
            raise AuthenticationError(_GENERIC_AUTH_FAILURE)
        access = self._tokens.issue_access_token(
            sub=member.id, tenant_id=member.tenant_id, user_id=member.id, role=member.role, now=now
        )
        return IssuedTokens(access_token=access, expires_in=ACCESS_TOKEN_TTL_SECONDS, refresh_token=minted.secret)

    refresh_browser = refresh_json

    # ----- access-token authentication -----
    def _authenticate_access_token_with_claims(self, token: str) -> tuple[Principal, Mapping[str, object]]:
        """Validate once, then reconcile the signed identity against canonical membership."""
        try:
            claims = self._tokens.validate_access_token(token)
        except TokenValidationError:
            raise AuthenticationError(_GENERIC_AUTH_FAILURE) from None
        tenant_id = claims["tenant_id"]
        user_id = claims["user_id"]
        user = self._repo.get_tenant_user_by_id(tenant_id, user_id)
        if user is None or not user.is_active:
            raise AuthenticationError(_GENERIC_AUTH_FAILURE)
        if str(user.tenant_id) != str(tenant_id) or user.role != claims["role"]:
            raise AuthenticationError(_GENERIC_AUTH_FAILURE)
        return Principal(tenant_id=str(user.tenant_id), user_id=str(user.id), role=user.role), claims

    def authenticate_access_token(self, token: str) -> Principal:
        """Validate an access token and reconcile it against the canonical identity."""
        principal, _claims = self._authenticate_access_token_with_claims(token)
        return principal

    def authorize_access_request(self, token: str) -> Principal:
        principal, claims = self._authenticate_access_token_with_claims(token)
        try:
            jti = str(uuid.UUID(str(claims["jti"])))
            issued_at = datetime.fromtimestamp(int(claims["iat"]), timezone.utc)
        except (KeyError, TypeError, ValueError, OverflowError, OSError):
            raise AuthenticationError(_GENERIC_AUTH_FAILURE) from None
        state = self._repo.access_state(principal.tenant_id, principal.user_id, jti, issued_at)
        if state is None or not state["is_active"]:
            raise AuthenticationError(_GENERIC_AUTH_FAILURE)
        if not state["tenant_active"]:
            raise TenantSuspendedError(_GENERIC_AUTH_FAILURE)
        if state["revoked"]:
            raise SessionRevokedError(_GENERIC_AUTH_FAILURE)
        return principal

    def revoke_token(self, access_token: str, refresh_token: str | None = None) -> None:
        """Revoke the validated access token and, when found, its refresh-token family."""
        _principal, claims = self._authenticate_access_token_with_claims(access_token)
        try:
            jti = str(uuid.UUID(str(claims["jti"])))
            expires_at = datetime.fromtimestamp(int(claims["exp"]), timezone.utc)
        except (KeyError, TypeError, ValueError, OverflowError, OSError):
            raise AuthenticationError(_GENERIC_AUTH_FAILURE) from None
        if isinstance(refresh_token, str) and refresh_token:
            try:
                refresh_size = len(refresh_token.encode("utf-8"))
            except UnicodeEncodeError:
                refresh_size = MAX_REFRESH_TOKEN_BYTES + 1
            if refresh_size <= MAX_REFRESH_TOKEN_BYTES:
                record = self._repo.get_refresh_token(self._refresh.digest(refresh_token))
                if record is not None:
                    self._repo.revoke_family(record.family_id, self._now())
        self._repo.revoke_access_token(jti, _principal.tenant_id, _principal.user_id, expires_at)

    def revoke_user_sessions(self, tenant_id: str, user_id: str) -> None:
        self._repo.revoke_user_sessions(tenant_id,user_id,self._now())
        self._audit(AuditEntry(tenant_id,user_id,"tenant_user","auth.sessions.revoked"))
    def request_password_reset(self, *, tenant_slug: str, email: str):
        user=self._repo.get_tenant_user_by_email(tenant_slug,email)
        if user is not None:
            secret=secrets.token_urlsafe(32); digest=self._reset.digest(secret); self._repo.issue_password_reset(user.tenant_id,user.id,digest,self._now()+__import__('datetime').timedelta(seconds=self._reset.ttl_seconds))
            self.sent_reset_secrets.append(secret)
            self._audit(AuditEntry(user.tenant_id,user.id,"tenant_user","auth.password_reset.issued"))
        return {"ok": True}
    def redeem_password_reset(self, *, secret: str, new_password: str, now: datetime | None = None):
        if not isinstance(secret,str) or not secret or len(secret)>8192: raise PasswordResetError(_GENERIC_AUTH_FAILURE)
        result=self._repo.consume_password_reset(self._reset.digest(secret),self._passwords.hash(new_password),now or self._now())
        if result is None: raise PasswordResetError(_GENERIC_AUTH_FAILURE)
        self._audit(AuditEntry(result[0],result[1],"tenant_user","auth.password_reset.consumed"))
    def begin_mfa_enrollment(self, *, tenant_id: str, user_id: str):
        user=self._repo.get_tenant_user_by_id(tenant_id,user_id)
        if not user or user.role not in ('owner','manager') or not self._mfa_cipher: raise MfaEnrollmentError('not permitted')
        secret=TOTPService().generate_secret(); self._pending_mfa[(tenant_id,user_id)]=secret
        from types import SimpleNamespace; return SimpleNamespace(secret=secret)
    def confirm_mfa_enrollment(self, *, tenant_id: str, user_id: str, code: str, now=None):
        secret=self._pending_mfa.get((tenant_id,user_id))
        if not secret or not TOTPService().verify(secret,code,now): raise MfaEnrollmentError('invalid')
        codes=RecoveryCodeService().generate(); self.mfa_recovery_codes=codes
        cipher=self._mfa_cipher.encrypt(secret.encode()); digests=[RecoveryCodeService().digest(x) for x in codes]
        self._repo.store_mfa(tenant_id,user_id,cipher,-1,digests)
        self._pending_mfa.pop((tenant_id,user_id), None)

    def public_jwks(self) -> dict:
        """The public JWKS document for this service's access-token signing keyring."""
        return self._tokens.public_jwks()

    # ----- helpers / fixtures -----
    def _issue(self, *, tenant_id: str, user_id: str, role: str, now: datetime) -> IssuedTokens:
        access = self._tokens.issue_access_token(
            sub=user_id, tenant_id=tenant_id, user_id=user_id, role=role, now=now
        )
        payload=access.split('.')[1]; claims=json.loads(base64.urlsafe_b64decode(payload+'='*(-len(payload)%4)))
        # JWT ``iat`` is second-granular.  If a session cutoff occurs mid-second, issue a
        # successor in the next representable second rather than accidentally invalidating
        # a newly authenticated session with the old token's cutoff.
        state = self._repo.access_state(tenant_id, user_id, str(claims['jti']), datetime.fromtimestamp(int(claims['iat']), timezone.utc))
        if state is not None and state['revoked']:
            # Do not mint a future-dated JWT: wait for the next representable ``iat``
            # second so normal JWT temporal validation remains strict.
            time.sleep(1.01)
            now = self._now()
            access = self._tokens.issue_access_token(sub=user_id, tenant_id=tenant_id, user_id=user_id, role=role, now=now)
        minted = self._refresh.mint(now=now)
        record = RefreshTokenRecord(
            id=str(uuid.uuid4()),
            family_id=str(uuid.uuid4()),  # a fresh login opens a new refresh family
            tenant_id=tenant_id,
            user_id=user_id,
            token_digest=minted.digest,
            issued_at=now,
            expires_at=minted.expires_at,
        )
        self._repo.insert_refresh_token(record)
        return IssuedTokens(access_token=access, expires_in=ACCESS_TOKEN_TTL_SECONDS, refresh_token=minted.secret)

    @classmethod
    def from_environment(
        cls, auth_conn, *, env: Mapping[str, str] | None = None, now: Callable[[], datetime] = _utcnow
    ) -> "TenantAuthService":
        """Wire the production tenant auth service over a trusted ``auth_svc`` connection.

        ``auth_conn`` MUST be a psycopg connection authenticated as the dedicated ``auth_svc``
        role (opened from its own ``AUTH_DATABASE_URL`` DSN) — the only role granted EXECUTE
        on the ``auth_*`` SECURITY DEFINER functions. It must NOT be a ``data_access.session``
        ``app_rw``/``app_ro`` connection: those roles have no access to the credential tables
        or auth functions at all, and anonymous login/refresh has no tenant context to open.
        RS256 keys, issuer/audience, and the refresh pepper load from the environment; a
        missing value is a hard failure, never an insecure fallback.
        """
        source = os.environ if env is None else env
        issuer = source.get(AUTH_JWT_ISSUER_ENV)
        audience = source.get(AUTH_JWT_AUDIENCE_ENV)
        if not issuer or not audience:
            raise JWTConfigurationError(f"{AUTH_JWT_ISSUER_ENV} and {AUTH_JWT_AUDIENCE_ENV} are required")
        token_service = RS256TokenService(
            RS256KeyRing.from_environment(source), issuer=issuer, audience=audience
        )
        service = cls(
            repository=DatabaseAuthRepository(auth_conn),
            password_service=Argon2idPasswordService(),
            token_service=token_service,
            refresh_token_service=OpaqueRefreshTokenService.from_environment(source),
            now=now,
            reset_service=PasswordResetService(source.get("AUTH_PASSWORD_RESET_PEPPER", ""), now, _positive_env_int(source, "AUTH_PASSWORD_RESET_TTL_SECONDS", 3600)),
            mfa_cipher=MfaSecretCipher.from_environment(source),
            throttle_max_failures=_positive_env_int(source, "AUTH_LOGIN_MAX_FAILURES", 5),
            throttle_window_seconds=_positive_env_int(source, "AUTH_LOGIN_LOCKOUT_SECONDS", 900),
        )
        return service

    @classmethod
    def for_contract_fixture(
        cls,
        *,
        user_is_active: bool = True,
        user_exists: bool = True,
        stored_password_hash: str | None = None,
        tenant_status: str = "active", role: str = "owner", mfa_env: Mapping[str,str] | None = None,
    ) -> "TenantAuthService":
        """Build a fully in-memory service for the CS-061a contract tests: the seeded
        ``maven-demo`` owner identity, an ephemeral RS256 keypair, and a test pepper.

        The three knobs exercise the safe-failure contract without a database:
        ``user_exists=False`` seeds no identity (an unknown-email login), ``user_is_active``
        seeds a deactivated identity, and ``stored_password_hash`` injects a damaged (non
        Argon2id) stored credential. All three must fail login identically.
        """
        passwords = Argon2idPasswordService()
        # When the test injects a damaged stored hash, use it verbatim so the service's
        # fail-closed InvalidPasswordHash path is exercised; otherwise hash the known secret.
        password_hash = (
            stored_password_hash
            if stored_password_hash is not None
            else passwords.hash("correct horse battery staple")
        )
        repository = InMemoryAuthRepository.with_contract_fixture(
            password_hash=password_hash,
            user_is_active=user_is_active,
            user_exists=user_exists,
            role=role,
        )
        service = cls(
            repository=repository,
            password_service=passwords,
            token_service=_build_contract_token_service(),
            # A high-entropy pepper (>= the 32-byte floor) for the in-memory fixture.
            refresh_token_service=OpaqueRefreshTokenService(pepper=secrets.token_urlsafe(32)),
            mfa_cipher=MfaSecretCipher.from_environment(mfa_env) if mfa_env else None,
            invite_ttl_seconds=_positive_env_int(os.environ, "INVITE_TTL", 72 * 60 * 60),
        )
        repository.set_tenant_status(InMemoryAuthRepository._FIXTURE_TENANT_ID, tenant_status)
        return service


def _build_contract_token_service() -> RS256TokenService:
    """An in-process RS256 keyring for tests — never a path for production keys, which are
    always environment-loaded via :meth:`RS256KeyRing.from_environment`."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("utf-8")
    public_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")
    kid = "cs061a-contract"
    key_ring = RS256KeyRing(
        signing_kid=kid, signing_private_pem=private_pem, public_pems={kid: public_pem}
    )
    return RS256TokenService(key_ring, issuer="cs061a-contract-issuer", audience="coffee-scheduler")
