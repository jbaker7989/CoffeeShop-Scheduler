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
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Mapping

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
    ConsumeStatus,
    DatabaseAuthRepository,
    InMemoryAuthRepository,
    RefreshTokenRecord,
)
from .tokens import MAX_REFRESH_TOKEN_BYTES, OpaqueRefreshTokenService

# Environment variable names for the production auth wiring. The auth service connects on
# its OWN dsn as the dedicated auth_svc role — never the data-access app_rw/app_ro roles.
AUTH_DATABASE_URL_ENV = "AUTH_DATABASE_URL"
AUTH_JWT_ISSUER_ENV = "AUTH_JWT_ISSUER"
AUTH_JWT_AUDIENCE_ENV = "AUTH_JWT_AUDIENCE"

# One opaque message for every credential/refresh failure: callers must not be able to
# distinguish "no such user" from "wrong password" from "inactive user".
_GENERIC_AUTH_FAILURE = "authentication failed"


class AuthenticationError(Exception):
    """A login, refresh, or access-token identity could not be authenticated."""


class RefreshReplayError(AuthenticationError):
    """A refresh token was reused after rotation (or its family was burned by a replay).

    A subclass of :class:`AuthenticationError` because a replay is an authentication
    failure, but distinguishable so callers can alarm on token theft specifically.
    """


@dataclass(frozen=True)
class IssuedTokens:
    """The result of a login or refresh. ``refresh_token`` is the raw opaque secret; the
    browser transport puts it in a cookie and omits it from the response body."""

    access_token: str
    expires_in: int
    refresh_token: str
    token_type: str = "bearer"


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
    ) -> None:
        self._repo = repository
        self._passwords = password_service
        self._tokens = token_service
        self._refresh = refresh_token_service
        self._now = now
        # A valid Argon2id hash of a random secret, verified against when the email lookup
        # misses so an unknown email costs the same Argon2 work as a wrong password. Without
        # it, response time leaks whether an email is registered (tenant/email enumeration).
        self._dummy_password_hash = password_service.hash(secrets.token_urlsafe(32))

    # ----- login -----
    def login_json(self, *, tenant_slug: str, email: str, password: str) -> IssuedTokens:
        """JSON/bearer login: verify active credentials, issue an access + refresh pair."""
        user = self._authenticate_credentials(tenant_slug=tenant_slug, email=email, password=password)
        now = self._now()
        self._repo.record_login(user.id, now)
        return self._issue(tenant_id=user.tenant_id, user_id=user.id, role=user.role, now=now)

    # The browser login shares identical credential semantics; the cookie/body transport
    # split is enforced in auth.api, keeping CSRF/token-exposure rules out of this layer.
    login_browser = login_json

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
    def authenticate_access_token(self, token: str) -> Principal:
        """Validate a bearer access token and reconcile it against the canonical identity.

        Signature/shape validation proves integrity; this method proves *authority* — the
        token's tenant and role must match the identity stored for its ``(tenant_id,
        user_id)``, or it is rejected before any F60 tenant context is opened."""
        try:
            claims = self._tokens.validate_access_token(token)
        except TokenValidationError:
            raise AuthenticationError(_GENERIC_AUTH_FAILURE) from None
        tenant_id = claims["tenant_id"]
        user_id = claims["user_id"]
        user = self._repo.get_tenant_user_by_id(tenant_id, user_id)
        if user is None or not user.is_active:
            raise AuthenticationError(_GENERIC_AUTH_FAILURE)
        # Canonical record is authoritative over the token's claims.
        if str(user.tenant_id) != str(tenant_id) or user.role != claims["role"]:
            raise AuthenticationError(_GENERIC_AUTH_FAILURE)
        return Principal(tenant_id=str(user.tenant_id), user_id=str(user.id), role=user.role)

    def public_jwks(self) -> dict:
        """The public JWKS document for this service's access-token signing keyring."""
        return self._tokens.public_jwks()

    # ----- helpers / fixtures -----
    def _issue(self, *, tenant_id: str, user_id: str, role: str, now: datetime) -> IssuedTokens:
        access = self._tokens.issue_access_token(
            sub=user_id, tenant_id=tenant_id, user_id=user_id, role=role, now=now
        )
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

    def issue_contract_token(
        self,
        *,
        tenant_id: str | None = None,
        user_id: str | None = None,
        role: str = "owner",
        claim_overrides: Mapping | None = None,
    ) -> str:
        """Mint a *correctly signed* access token with caller-chosen claims, for the
        adversarial contract only.

        Two uses, both proving :meth:`authenticate_access_token` rejects a well-signed token
        on something other than its signature:

        * ``claim_overrides`` — start from a fully valid tenant claim set for the seeded
          fixture identity, then overwrite individual claims (a wrong issuer/audience, an
          expired ``exp``, a future ``nbf``, an empty ``jti``, a non-UUID id, a disallowed
          ``role``, an ``is_saas_admin: True``, a mismatched ``sub``). Validation must fail
          closed on the tampered claim even though the signature and ``kid`` are genuine.
        * ``tenant_id``/``user_id``/``role`` — a valid, well-formed token whose
          ``(tenant_id, user_id)`` is not a real membership, proving canonical reconciliation
          rejects a cross-tenant/role escape.

        It is never a login path; production access tokens come only from the credentialed
        login/refresh flows via :meth:`RS256TokenService.issue_access_token`.
        """
        uid = str(user_id) if user_id is not None else InMemoryAuthRepository._FIXTURE_USER_ID
        tid = str(tenant_id) if tenant_id is not None else InMemoryAuthRepository._FIXTURE_TENANT_ID
        iat = int(self._now().timestamp())
        claims = {
            "iss": self._tokens.issuer,
            "aud": self._tokens.audience,
            "sub": uid,
            "tenant_id": tid,
            "user_id": uid,
            "role": role,
            "is_saas_admin": False,
            "jti": str(uuid.uuid4()),
            "iat": iat,
            "nbf": iat,
            "exp": iat + self._tokens.ttl_seconds,
        }
        if claim_overrides:
            claims.update(claim_overrides)
        return self._tokens.sign_unchecked(claims)

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
        return cls(
            repository=DatabaseAuthRepository(auth_conn),
            password_service=Argon2idPasswordService(),
            token_service=token_service,
            refresh_token_service=OpaqueRefreshTokenService.from_environment(source),
            now=now,
        )

    @classmethod
    def for_contract_fixture(
        cls,
        *,
        user_is_active: bool = True,
        user_exists: bool = True,
        stored_password_hash: str | None = None,
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
        )
        return cls(
            repository=repository,
            password_service=passwords,
            token_service=_build_contract_token_service(),
            # A high-entropy pepper (>= the 32-byte floor) for the in-memory fixture.
            refresh_token_service=OpaqueRefreshTokenService(pepper=secrets.token_urlsafe(32)),
        )


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
