"""Persistence for CS-061a tenant identities and refresh-token families (F61 AC1/AC2).

This module owns *storage only*. It deliberately does **not** mint tokens, hash
passwords, issue JWTs, or decide which exception a web layer raises — those live in the
sibling slice (``auth.tokens`` / ``auth.service`` / ``auth.jwt`` / ``auth.api``). What
it provides:

- ``TenantUserRecord`` / ``RefreshTokenRecord`` — plain row shapes.
- ``AuthRepository`` — the abstract persistence interface the auth service codes against.
- ``InMemoryAuthRepository`` — a dependency-free fixture store (used by the contract
  tests via ``TenantAuthService.for_contract_fixture``).
- ``DatabaseAuthRepository`` — a psycopg3-backed store that reaches credential rows only
  through the migration's ``auth_*`` SECURITY DEFINER functions. ``app_rw`` holds no direct
  DML on the credential tables (which would expose every same-tenant ``password_hash`` /
  ``token_digest``); the functions resolve the tenant server-side, so anonymous
  login/refresh works without a caller-supplied RLS context.

### Refresh-token rotation and replay (the atomic contract)

``consume_and_replace`` is the single atomic "use this refresh token once" operation.
Its outcome tells the service what happened; the service maps it to its own exceptions:

| ``ConsumeStatus`` | Meaning                                   | ``replayed`` |
| ----------------- | ----------------------------------------- | ------------ |
| ``ROTATED``       | token was valid; child issued, parent marked rotated | False |
| ``REPLAY``        | token was already rotated — reuse detected; family revoked | True  |
| ``REVOKED``       | token belongs to an already-revoked family | True  |
| ``EXPIRED``       | token is past ``expires_at``              | False |
| ``NOT_FOUND``     | no row for the presented digest           | False |

**In CS-061a the only revocation path is family replay** (there is no logout or
revocation-list endpoint yet — CS-061b owns those). So both ``REPLAY`` and ``REVOKED``
mean "this family was burned by a replay": the service raises ``RefreshReplayError``
whenever ``result.replayed`` is true, and ``AuthenticationError`` for ``EXPIRED`` /
``NOT_FOUND``. Detecting reuse and revoking the whole family happen inside one
transaction here, because atomicity is a storage property.

Digests, not raw tokens, are stored and looked up. Lookup is *keyed* by digest (dict
key / unique index), so no secret-dependent comparison branch exists to leak timing.
"""
from __future__ import annotations

import abc
import enum
from dataclasses import dataclass, replace
from datetime import datetime, timezone

from psycopg.rows import dict_row


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class TenantUserRecord:
    """A tenant-local login identity. ``password_hash`` is an Argon2id encoded hash."""

    id: str
    tenant_id: str
    tenant_slug: str
    email: str
    password_hash: str
    role: str
    is_active: bool


@dataclass(frozen=True)
class TenantMembershipRecord:
    """Canonical membership for tenant-claim reconciliation (F61.ac2) — no secrets.

    Deliberately carries only the fields the auth service needs to check a signed token
    against the database of record: that the user exists in the claimed tenant, with the
    claimed role, and is active. It never exposes ``password_hash``, ``email``, or any
    token digest, so the by-id lookup cannot be used to harvest credentials.
    """

    id: str
    tenant_id: str
    role: str
    is_active: bool


@dataclass(frozen=True)
class RefreshTokenRecord:
    """One server-side refresh-token record, identified by a one-way ``token_digest``."""

    id: str
    family_id: str
    tenant_id: str
    user_id: str
    token_digest: str
    issued_at: datetime
    expires_at: datetime
    rotated_at: datetime | None = None
    revoked_at: datetime | None = None


class ConsumeStatus(enum.Enum):
    ROTATED = "rotated"
    REPLAY = "replay"
    REVOKED = "revoked"
    EXPIRED = "expired"
    NOT_FOUND = "not_found"


@dataclass(frozen=True)
class ConsumeResult:
    """Outcome of ``consume_and_replace``. ``child`` is set only when ``ROTATED``."""

    status: ConsumeStatus
    child: RefreshTokenRecord | None = None

    @property
    def replayed(self) -> bool:
        """True when the presented token signals a burned family (see module docs).

        CS-061a has no revocation path other than replay, so the service treats both
        ``REPLAY`` and ``REVOKED`` as a replay event (``RefreshReplayError``).
        """
        return self.status in (ConsumeStatus.REPLAY, ConsumeStatus.REVOKED)


class AuthRepository(abc.ABC):
    """Persistence interface for tenant auth. Implementations must not add auth policy."""

    # ----- identities -----
    @abc.abstractmethod
    def get_tenant_user_by_email(self, tenant_slug: str, email: str) -> TenantUserRecord | None:
        """Return the identity for ``(tenant_slug, email)``, or None. Email is
        case-insensitive (CITEXT). Returns inactive users too; the *service* decides
        that an inactive user fails login indistinguishably from a bad credential."""

    @abc.abstractmethod
    def get_tenant_user_by_id(self, tenant_id: str, user_id: str) -> TenantMembershipRecord | None:
        """Return the canonical membership for ``(tenant_id, user_id)``, or None if that
        user is not in that tenant. Used for the strict tenant-claim-authority rule
        (F61.ac2): a validly signed token whose ``(tenant_id, user_id)`` is not a real
        membership resolves to None and is rejected. Returns no secret — see
        ``TenantMembershipRecord``."""

    @abc.abstractmethod
    def record_login(self, user_id: str, when: datetime | None = None) -> None:
        """Stamp ``last_login_at`` for a successful login."""

    # ----- refresh-token families -----
    @abc.abstractmethod
    def insert_refresh_token(self, record: RefreshTokenRecord) -> None:
        """Persist a freshly issued refresh-token record (digest only)."""

    @abc.abstractmethod
    def get_refresh_token(self, token_digest: str) -> RefreshTokenRecord | None:
        """Fetch a record by digest, or None."""

    @abc.abstractmethod
    def consume_and_replace(
        self, *, presented_digest: str, child: RefreshTokenRecord, now: datetime | None = None
    ) -> ConsumeResult:
        """Atomically single-use the presented token and persist its ``child`` replacement.

        The child's ``family_id``, ``tenant_id`` and ``user_id`` are **derived from the
        presented parent**, not from the passed ``child`` (those three fields on the input
        are ignored): a caller cannot re-home a refresh family to another tenant/user even
        by accident. Only the child's ``id``, ``token_digest``, ``issued_at`` and
        ``expires_at`` are honoured. On a ``ROTATED`` result ``child`` carries the canonical
        ``family_id``/``tenant_id``/``user_id``. On replay (presented token already
        rotated/revoked), revoke the entire family in the same transaction and return a
        ``replayed`` result without persisting ``child``.
        """

    @abc.abstractmethod
    def revoke_family(self, family_id: str, when: datetime | None = None) -> int:
        """Revoke every not-yet-revoked token in a family. Returns rows affected."""


class InMemoryAuthRepository(AuthRepository):
    """Process-local store for tests and the CS-061a contract fixture.

    Single-threaded dict operations make the "atomic" consume-and-replace trivially
    atomic here; ``DatabaseAuthRepository`` achieves the same with ``SELECT ... FOR UPDATE``.
    """

    # The contract fixture mirrors the seed tenant created in migration 0001.
    _FIXTURE_TENANT_ID = "00000000-0000-0000-0000-000000000001"
    _FIXTURE_TENANT_SLUG = "maven-demo"
    _FIXTURE_USER_ID = "00000000-0000-0000-0000-000000000001"
    _FIXTURE_EMAIL = "owner@maven.example"

    def __init__(self) -> None:
        self._users: dict[tuple[str, str], TenantUserRecord] = {}
        self._tokens: dict[str, RefreshTokenRecord] = {}

    # ----- fixture helpers -----
    def add_user(self, user: TenantUserRecord) -> None:
        self._users[(user.tenant_slug, user.email.lower())] = user

    @classmethod
    def with_contract_fixture(
        cls,
        *,
        password_hash: str,
        user_is_active: bool = True,
        user_exists: bool = True,
        role: str = "owner",
    ) -> "InMemoryAuthRepository":
        """Seed the single ``maven-demo`` owner identity the contract tests expect.

        The caller supplies ``password_hash`` (``Argon2idPasswordService().hash(...)``)
        so this storage layer never touches raw passwords. ``user_exists=False`` seeds an
        empty store so an email lookup misses — the unknown-user login path.
        """
        repo = cls()
        if not user_exists:
            # No identity seeded: get_tenant_user_by_email returns None, so the service runs
            # its dummy-verify timing-equalisation branch and fails like a wrong password.
            return repo
        repo.add_user(
            TenantUserRecord(
                id=cls._FIXTURE_USER_ID,
                tenant_id=cls._FIXTURE_TENANT_ID,
                tenant_slug=cls._FIXTURE_TENANT_SLUG,
                email=cls._FIXTURE_EMAIL,
                password_hash=password_hash,
                role=role,
                is_active=user_is_active,
            )
        )
        return repo

    # ----- identities -----
    def get_tenant_user_by_email(self, tenant_slug: str, email: str) -> TenantUserRecord | None:
        return self._users.get((tenant_slug, email.lower()))

    def get_tenant_user_by_id(self, tenant_id: str, user_id: str) -> TenantMembershipRecord | None:
        for user in self._users.values():
            if user.id == user_id and user.tenant_id == tenant_id:
                return TenantMembershipRecord(
                    id=user.id, tenant_id=user.tenant_id, role=user.role, is_active=user.is_active
                )
        return None

    def record_login(self, user_id: str, when: datetime | None = None) -> None:
        # last_login_at is not read back by the contract; keep the write a no-op-safe stamp.
        for key, user in self._users.items():
            if user.id == user_id:
                return  # nothing to mutate on the frozen record for in-memory use

    # ----- refresh-token families -----
    def insert_refresh_token(self, record: RefreshTokenRecord) -> None:
        if record.token_digest in self._tokens:
            raise ValueError("duplicate refresh-token digest")
        self._tokens[record.token_digest] = record

    def get_refresh_token(self, token_digest: str) -> RefreshTokenRecord | None:
        return self._tokens.get(token_digest)

    def consume_and_replace(
        self, *, presented_digest: str, child: RefreshTokenRecord, now: datetime | None = None
    ) -> ConsumeResult:
        now = now or _utcnow()
        presented = self._tokens.get(presented_digest)
        if presented is None:
            return ConsumeResult(ConsumeStatus.NOT_FOUND)
        if presented.revoked_at is not None:
            self.revoke_family(presented.family_id, now)
            return ConsumeResult(ConsumeStatus.REVOKED)
        if presented.rotated_at is not None:
            # Reuse of an already-rotated token: burn the whole family atomically.
            self.revoke_family(presented.family_id, now)
            return ConsumeResult(ConsumeStatus.REPLAY)
        if presented.expires_at <= now:
            return ConsumeResult(ConsumeStatus.EXPIRED)
        # The child inherits family/tenant/user from the parent (never the caller's copy),
        # so a rotation can never re-home the family to another tenant or user.
        canonical_child = replace(
            child,
            family_id=presented.family_id,
            tenant_id=presented.tenant_id,
            user_id=presented.user_id,
        )
        self._tokens[presented_digest] = replace(presented, rotated_at=now)
        self.insert_refresh_token(canonical_child)
        return ConsumeResult(ConsumeStatus.ROTATED, canonical_child)

    def revoke_family(self, family_id: str, when: datetime | None = None) -> int:
        when = when or _utcnow()
        affected = 0
        for digest, token in list(self._tokens.items()):
            if token.family_id == family_id and token.revoked_at is None:
                self._tokens[digest] = replace(token, revoked_at=when)
                affected += 1
        return affected


_CONSUME_STATUS = {
    "ROTATED": ConsumeStatus.ROTATED,
    "REPLAY": ConsumeStatus.REPLAY,
    "REVOKED": ConsumeStatus.REVOKED,
    "EXPIRED": ConsumeStatus.EXPIRED,
    "NOT_FOUND": ConsumeStatus.NOT_FOUND,
}


class DatabaseAuthRepository(AuthRepository):
    """psycopg3-backed store that reaches credential rows only through the migration's
    ``auth_*`` SECURITY DEFINER functions.

    **Trusted-service port.** The connection must run as the dedicated ``auth_svc`` role,
    which is the *only* role granted ``EXECUTE`` on the ``auth_*`` functions. ``app_rw`` /
    ``app_ro`` (the general data-access layer's roles) have no access to these functions or
    to the credential tables at all — so this repository must not be constructed over a
    general ``data_access.session`` connection. ``auth_find_tenant_user`` returns a
    ``password_hash`` for any ``(slug, email)``, so confining it to the auth service's own
    role keeps ordinary application code from harvesting credentials.

    No direct DML on ``tenant_users`` / ``auth_refresh_tokens`` is used: anonymous
    login/refresh cannot establish a trusted tenant RLS context up front, and direct
    ``SELECT`` would expose every same-tenant ``password_hash`` / ``token_digest``. The
    functions encapsulate exactly the needed parameterised queries and resolve the tenant
    server-side, so this class sets no ``app.tenant_id`` and uses no caller-supplied filter.
    """

    def __init__(self, conn) -> None:
        self.conn = conn

    def get_tenant_user_by_email(self, tenant_slug: str, email: str) -> TenantUserRecord | None:
        with self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT id, tenant_id, tenant_slug, email, password_hash, role, is_active "
                "FROM auth_find_tenant_user(%s, %s)",
                (tenant_slug, email),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return TenantUserRecord(
            id=str(row["id"]),
            tenant_id=str(row["tenant_id"]),
            tenant_slug=row["tenant_slug"],
            email=row["email"],
            password_hash=row["password_hash"],
            role=row["role"],
            is_active=row["is_active"],
        )

    def get_tenant_user_by_id(self, tenant_id: str, user_id: str) -> TenantMembershipRecord | None:
        with self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT id, tenant_id, role, is_active FROM auth_get_tenant_user_by_id(%s, %s)",
                (tenant_id, user_id),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return TenantMembershipRecord(
            id=str(row["id"]),
            tenant_id=str(row["tenant_id"]),
            role=row["role"],
            is_active=row["is_active"],
        )

    def record_login(self, user_id: str, when: datetime | None = None) -> None:
        with self.conn.cursor() as cur:
            cur.execute("SELECT auth_record_login(%s, %s)", (user_id, when))

    def insert_refresh_token(self, record: RefreshTokenRecord) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT auth_issue_refresh_token(%s, %s, %s, %s, %s, %s, %s)",
                (
                    record.id, record.family_id, record.tenant_id, record.user_id,
                    record.token_digest, record.issued_at, record.expires_at,
                ),
            )

    def get_refresh_token(self, token_digest: str) -> RefreshTokenRecord | None:
        with self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM auth_get_refresh_token(%s)", (token_digest,))
            row = cur.fetchone()
        return None if row is None else self._to_record(row)

    def consume_and_replace(
        self, *, presented_digest: str, child: RefreshTokenRecord, now: datetime | None = None
    ) -> ConsumeResult:
        now = now or _utcnow()
        # The whole single-use-then-rotate-or-burn decision is one atomic statement in
        # auth_consume_refresh_token (SELECT ... FOR UPDATE + conditional write). The child's
        # family/tenant/user are derived from the parent *inside* the function, so only the
        # child id/digest/issued/expires are passed and the family can never be re-homed.
        with self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT status, family_id, tenant_id, user_id "
                "FROM auth_consume_refresh_token(%s, %s, %s, %s, %s, %s)",
                (
                    presented_digest, child.id, child.token_digest,
                    child.issued_at, child.expires_at, now,
                ),
            )
            row = cur.fetchone()
        status = _CONSUME_STATUS[row["status"]]
        if status is not ConsumeStatus.ROTATED:
            return ConsumeResult(status)
        # Reflect the canonical family/tenant/user the function assigned to the child.
        canonical_child = replace(
            child,
            family_id=str(row["family_id"]),
            tenant_id=str(row["tenant_id"]),
            user_id=str(row["user_id"]),
        )
        return ConsumeResult(status, canonical_child)

    def revoke_family(self, family_id: str, when: datetime | None = None) -> int:
        when = when or _utcnow()
        with self.conn.cursor() as cur:
            cur.execute("SELECT auth_revoke_refresh_family(%s, %s)", (family_id, when))
            return cur.fetchone()[0]

    @staticmethod
    def _to_record(row: dict) -> RefreshTokenRecord:
        return RefreshTokenRecord(
            id=str(row["id"]),
            family_id=str(row["family_id"]),
            tenant_id=str(row["tenant_id"]),
            user_id=str(row["user_id"]),
            token_digest=row["token_digest"],
            issued_at=row["issued_at"],
            expires_at=row["expires_at"],
            rotated_at=row["rotated_at"],
            revoked_at=row["revoked_at"],
        )
