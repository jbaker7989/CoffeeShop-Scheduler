"""Tenant-scoped, lock-protected login failure limiter."""
from __future__ import annotations
import threading
import ipaddress
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
    def client_ip_bucket(self):
        """Return a canonical, explicitly tagged bucket for one client IP."""
        try:
            address = ipaddress.ip_address(self.ip)
        except ValueError as exc:
            raise ValueError("client IP must be a valid IPv4 or IPv6 address") from exc
        return ("client_ip", address.compressed)
class LoginRateLimiter:
    def __init__(self): self._fails = {}; self._locks = {}; self._lock = threading.RLock()
    def _now(self, now): return now or datetime.now(timezone.utc)
    def _prune(self, account, now):
        self._fails[account] = [x for x in self._fails.get(account, []) if x > now - timedelta(seconds=LOCKOUT_WINDOW_SECONDS)]
    def check(self, key, now=None):
        self._check_bucket(key.account(), now)
    def check_ip(self, key, now=None):
        self._check_bucket(key.client_ip_bucket(), now)
    def _check_bucket(self, bucket, now):
        now=self._now(now)
        with self._lock:
            until=self._locks.get(bucket)
            if until and until > now: raise AccountLockedError("authentication failed")
            if until: self._locks.pop(bucket, None); self._fails.pop(bucket, None)
            self._prune(bucket, now)
    def record_failure(self, key, now=None):
        self._record_failure(key.account(), now)
    def record_ip_failure(self, key, now=None):
        self._record_failure(key.client_ip_bucket(), now)
    def _record_failure(self, bucket, now):
        now=self._now(now)
        with self._lock:
            self._prune(bucket, now); failures=self._fails.setdefault(bucket, []); failures.append(now)
            if len(failures) >= MAX_FAILED_ATTEMPTS: self._locks[bucket] = now + timedelta(seconds=LOCKOUT_DURATION_SECONDS)
    def record_success(self, key, now=None):
        with self._lock: self._fails.pop(key.account(), None); self._locks.pop(key.account(), None)
    def record_ip_success(self, key, now=None):
        bucket = key.client_ip_bucket()
        with self._lock:
            self._fails.pop(bucket, None)
            self._locks.pop(bucket, None)
