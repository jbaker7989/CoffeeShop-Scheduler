"""Lock-protected account and client-IP failure buckets for authentication flows."""
from __future__ import annotations

import ipaddress
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TypeAlias

MAX_FAILED_ATTEMPTS = 5
LOCKOUT_WINDOW_SECONDS = 900
LOCKOUT_DURATION_SECONDS = 900

BucketKey: TypeAlias = tuple[str, ...]


class AccountLockedError(Exception):
    """Raised when a failure bucket is currently locked."""


@dataclass(frozen=True)
class LoginAttemptKey:
    tenant_id: str
    email: str
    ip: str

    def account(self) -> BucketKey:
        """Return the tenant-scoped account bucket."""
        return (self.tenant_id, self.email.lower())

    def client_ip_bucket(self) -> BucketKey:
        """Return a canonical, explicitly tagged bucket for one client IP."""
        try:
            address = ipaddress.ip_address(self.ip)
        except ValueError as exc:
            raise ValueError("client IP must be a valid IPv4 or IPv6 address") from exc
        return ("client_ip", address.compressed)


class LoginRateLimiter:
    """Apply a bounded rolling failure window to account or client-IP buckets."""

    def __init__(self) -> None:
        self._fails: dict[BucketKey, list[datetime]] = {}
        self._locks: dict[BucketKey, datetime] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _now(now: datetime | None) -> datetime:
        return now or datetime.now(timezone.utc)

    def _prune(self, bucket: BucketKey, now: datetime) -> None:
        cutoff = now - timedelta(seconds=LOCKOUT_WINDOW_SECONDS)
        self._fails[bucket] = [
            failure_time
            for failure_time in self._fails.get(bucket, [])
            if failure_time > cutoff
        ]

    def check(self, key: LoginAttemptKey, now: datetime | None = None) -> None:
        self._check_bucket(key.account(), now)

    def check_ip(self, key: LoginAttemptKey, now: datetime | None = None) -> None:
        self._check_bucket(key.client_ip_bucket(), now)

    def _check_bucket(self, bucket: BucketKey, now: datetime | None) -> None:
        checked_at = self._now(now)
        with self._lock:
            locked_until = self._locks.get(bucket)
            if locked_until is not None and locked_until > checked_at:
                raise AccountLockedError("authentication failed")
            if locked_until is not None:
                self._locks.pop(bucket, None)
                self._fails.pop(bucket, None)
            self._prune(bucket, checked_at)

    def record_failure(self, key: LoginAttemptKey, now: datetime | None = None) -> None:
        self._record_failure(key.account(), now)

    def record_ip_failure(self, key: LoginAttemptKey, now: datetime | None = None) -> None:
        self._record_failure(key.client_ip_bucket(), now)

    def _record_failure(self, bucket: BucketKey, now: datetime | None) -> None:
        failed_at = self._now(now)
        with self._lock:
            self._prune(bucket, failed_at)
            failures = self._fails.setdefault(bucket, [])
            failures.append(failed_at)
            if len(failures) >= MAX_FAILED_ATTEMPTS:
                self._locks[bucket] = failed_at + timedelta(
                    seconds=LOCKOUT_DURATION_SECONDS
                )

    def record_success(self, key: LoginAttemptKey, now: datetime | None = None) -> None:
        bucket = key.account()
        with self._lock:
            self._fails.pop(bucket, None)
            self._locks.pop(bucket, None)

    def record_ip_success(self, key: LoginAttemptKey, now: datetime | None = None) -> None:
        bucket = key.client_ip_bucket()
        with self._lock:
            self._fails.pop(bucket, None)
            self._locks.pop(bucket, None)
