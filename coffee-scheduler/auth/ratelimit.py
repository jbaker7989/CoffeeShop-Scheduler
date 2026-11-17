"""Tenant-scoped, lock-protected login failure limiter."""
from __future__ import annotations
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

MAX_FAILED_ATTEMPTS = 5
LOCKOUT_WINDOW_SECONDS = 900
LOCKOUT_DURATION_SECONDS = 900
class AccountLockedError(Exception): pass
@dataclass(frozen=True)
class LoginAttemptKey:
    tenant_id: str
    email: str
    ip: str
    def account(self): return (self.tenant_id, self.email.lower())
class LoginRateLimiter:
    def __init__(self): self._fails = {}; self._locks = {}; self._lock = threading.RLock()
    def _now(self, now): return now or datetime.now(timezone.utc)
    def _prune(self, account, now):
        self._fails[account] = [x for x in self._fails.get(account, []) if x > now - timedelta(seconds=LOCKOUT_WINDOW_SECONDS)]
    def check(self, key, now=None):
        now=self._now(now); account=key.account()
        with self._lock:
            until=self._locks.get(account)
            if until and until > now: raise AccountLockedError("authentication failed")
            if until: self._locks.pop(account, None); self._fails.pop(account, None)
            self._prune(account, now)
    def record_failure(self, key, now=None):
        now=self._now(now); account=key.account()
        with self._lock:
            self._prune(account, now); failures=self._fails.setdefault(account, []); failures.append(now)
            if len(failures) >= MAX_FAILED_ATTEMPTS: self._locks[account] = now + timedelta(seconds=LOCKOUT_DURATION_SECONDS)
    def record_success(self, key, now=None):
        with self._lock: self._fails.pop(key.account(), None); self._locks.pop(key.account(), None)
