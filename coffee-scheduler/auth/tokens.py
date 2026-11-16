"""Opaque refresh-token primitives for CS-061a (F61 AC2).

Refresh tokens are random opaque secrets, **never** JWTs. Only a one-way, peppered
digest is ever handed to the storage repository; the raw secret and the pepper never
leave this process and are never persisted or logged.

Rotation/replay *authority* lives in the storage repository, which performs the atomic
consume-and-replace over a token family. This module owns only the crypto (mint a secret,
derive its peppered digest, constant-time compare) and the small vocabulary — the
:class:`ConsumeOutcome` statuses — that the service and the repository share so neither
has to import the other's internals.
"""
from __future__ import annotations

import hmac
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Callable, Mapping

# 7-day refresh lifetime, fixed from each rotation with no sliding extension (F61.ac2).
REFRESH_TOKEN_TTL_SECONDS = 7 * 24 * 60 * 60
# Entropy of the opaque secret before URL-safe encoding.
REFRESH_TOKEN_SECRET_BYTES = 32
# The pepper is a keyed-hash secret; too short a pepper is brute-forceable, so require at
# least 256 bits of key material. This is a floor, not a recommendation.
MIN_PEPPER_BYTES = 32
# Upper bound on a presented refresh secret we will digest. Our own secrets are ~43 chars;
# anything far larger is a malformed/abusive input and is rejected unread (DoS guard).
MAX_REFRESH_TOKEN_BYTES = 4096

# Outcome statuses the storage repository returns from consume_and_replace. They are the
# contract between the repository (which decides) and the service (which maps to errors).
REFRESH_ROTATED = "rotated"
REFRESH_REPLAYED = "replayed"
REFRESH_REJECTED = "rejected"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class MintedRefreshToken:
    """A freshly minted refresh token: the raw ``secret`` goes to the client exactly once,
    the ``digest`` is the only thing the repository ever stores."""

    secret: str
    digest: str
    expires_at: datetime


@dataclass(frozen=True)
class ConsumeOutcome:
    """The repository's verdict on a presented refresh token.

    ``status`` is one of :data:`REFRESH_ROTATED`, :data:`REFRESH_REPLAYED`,
    :data:`REFRESH_REJECTED`. On ``rotated`` the identity fields are populated so the
    service can re-check canonical membership before minting the next access token.
    """

    status: str
    tenant_id: str | None = None
    user_id: str | None = None
    family_id: str | None = None


class OpaqueRefreshTokenService:
    """Mints opaque refresh secrets and derives their peppered digests.

    The pepper is a server-side secret supplied by the environment; it is combined with
    each secret via HMAC-SHA256 so a database leak of digests cannot be reversed or
    pre-computed. Comparison is constant time.
    """

    def __init__(
        self,
        *,
        pepper: bytes | str,
        ttl_seconds: int = REFRESH_TOKEN_TTL_SECONDS,
        now: Callable[[], datetime] = _utcnow,
    ) -> None:
        pepper_bytes = pepper.encode("utf-8") if isinstance(pepper, str) else pepper
        if len(pepper_bytes) < MIN_PEPPER_BYTES:
            # No insecure fallback: without a long, high-entropy pepper the digest is
            # brute-forceable, so a short/empty pepper is a hard configuration failure.
            raise ValueError(f"refresh-token pepper must be at least {MIN_PEPPER_BYTES} bytes")
        self._pepper = pepper_bytes
        self._ttl_seconds = ttl_seconds
        self._now = now

    @classmethod
    def from_environment(
        cls,
        env: Mapping[str, str] | None = None,
        *,
        AUTH_REFRESH_PEPPER: str = "AUTH_REFRESH_PEPPER",
        ttl_seconds: int = REFRESH_TOKEN_TTL_SECONDS,
    ) -> "OpaqueRefreshTokenService":
        source = os.environ if env is None else env
        pepper = source.get(AUTH_REFRESH_PEPPER)
        if not pepper:
            raise ValueError(f"{AUTH_REFRESH_PEPPER} is not set")
        return cls(pepper=pepper, ttl_seconds=ttl_seconds)

    def digest(self, secret: str) -> str:
        """One-way, peppered digest of a refresh secret. Deterministic so the repository
        can key on it; non-reversible so a stored digest never yields the secret."""
        return hmac.new(self._pepper, secret.encode("utf-8"), sha256).hexdigest()

    def verify_match(self, secret: str, digest: str) -> bool:
        """Constant-time check that ``secret`` digests to ``digest``."""
        return hmac.compare_digest(self.digest(secret), digest)

    def mint(self, *, now: datetime | None = None) -> MintedRefreshToken:
        issued = now or self._now()
        secret = secrets.token_urlsafe(REFRESH_TOKEN_SECRET_BYTES)
        return MintedRefreshToken(
            secret=secret,
            digest=self.digest(secret),
            expires_at=issued + timedelta(seconds=self._ttl_seconds),
        )
