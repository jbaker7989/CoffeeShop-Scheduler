"""Peppered, single-use password reset token handling."""
from __future__ import annotations
import hashlib, hmac, secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
PASSWORD_RESET_TTL_SECONDS = 3600
class PasswordResetError(Exception): pass
@dataclass
class ResetToken: digest: str; tenant_id: str; user_id: str; expires_at: datetime; consumed: bool=False
class PasswordResetService:
    def __init__(self, pepper: str, now=lambda: datetime.now(timezone.utc), ttl_seconds: int = PASSWORD_RESET_TTL_SECONDS):
        if not isinstance(pepper, str) or len(pepper) < 32: raise ValueError("AUTH_PASSWORD_RESET_PEPPER is required")
        if not isinstance(ttl_seconds, int) or ttl_seconds <= 0: raise ValueError("AUTH_PASSWORD_RESET_TTL_SECONDS must be positive")
        self.pepper, self.now, self.ttl_seconds, self.tokens = pepper.encode(), now, ttl_seconds, {}
    def digest(self, secret): return hmac.new(self.pepper, secret.encode(), hashlib.sha256).hexdigest()
    def request_reset(self, tenant_id, user_id):
        for t in self.tokens.values():
            if t.tenant_id == tenant_id and t.user_id == user_id: t.consumed=True
        secret=secrets.token_urlsafe(32); self.tokens[self.digest(secret)] = ResetToken(self.digest(secret), tenant_id, user_id, self.now()+timedelta(seconds=self.ttl_seconds)); return secret
    def redeem_reset(self, secret, now=None):
        if not isinstance(secret,str) or not secret or len(secret)>8192: raise PasswordResetError("authentication failed")
        token=self.tokens.get(self.digest(secret)); now=now or self.now()
        if token is None or token.consumed or token.expires_at < now: raise PasswordResetError("authentication failed")
        token.consumed=True; return token
