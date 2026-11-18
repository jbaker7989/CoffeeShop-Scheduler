"""Peppered, single-use password-reset token helpers."""
from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

PASSWORD_RESET_TTL_SECONDS = 3600


class PasswordResetError(Exception):
    """Raised when a reset secret is malformed, expired, or already consumed."""


@dataclass
class ResetToken:
    digest: str
    tenant_id: str
    user_id: str
    expires_at: datetime
    consumed: bool = False


class PasswordResetService:
    """Mint and verify reset secrets using an environment-provided pepper."""

    def __init__(
        self,
        pepper: str,
        now: Callable[[], datetime] | None = None,
        ttl_seconds: int = PASSWORD_RESET_TTL_SECONDS,
    ) -> None:
        if not isinstance(pepper, str) or len(pepper) < 32:
            raise ValueError("AUTH_PASSWORD_RESET_PEPPER is required")
        if not isinstance(ttl_seconds, int) or ttl_seconds <= 0:
            raise ValueError("AUTH_PASSWORD_RESET_TTL_SECONDS must be positive")

        self.pepper = pepper.encode()
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.ttl_seconds = ttl_seconds
        self.tokens: dict[str, ResetToken] = {}

    def digest(self, secret: str) -> str:
        return hmac.new(
            self.pepper,
            secret.encode(),
            hashlib.sha256,
        ).hexdigest()

    def request_reset(self, tenant_id: str, user_id: str) -> str:
        for token in self.tokens.values():
            if token.tenant_id == tenant_id and token.user_id == user_id:
                token.consumed = True

        secret = secrets.token_urlsafe(32)
        token_digest = self.digest(secret)
        expires_at = self.now() + timedelta(seconds=self.ttl_seconds)
        self.tokens[token_digest] = ResetToken(
            digest=token_digest,
            tenant_id=tenant_id,
            user_id=user_id,
            expires_at=expires_at,
        )
        return secret

    def redeem_reset(
        self,
        secret: str,
        now: datetime | None = None,
    ) -> ResetToken:
        if not isinstance(secret, str) or not secret or len(secret) > 8192:
            raise PasswordResetError("authentication failed")

        token = self.tokens.get(self.digest(secret))
        checked_at = now or self.now()
        if token is None or token.consumed or token.expires_at < checked_at:
            raise PasswordResetError("authentication failed")

        token.consumed = True
        return token
