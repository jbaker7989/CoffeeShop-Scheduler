"""CS-061b / F61 AC4 login rate-limiting / lockout contracts; red before implementation.

Lockout counts failures on the account ``(tenant_id, email)`` and, independently, on the
client IP. It engages at a threshold inside a window, expires after a lockout duration,
resets on a successful login, and is strictly tenant-scoped so it cannot become a
cross-tenant denial-of-service. Concurrent failures are counted without lost updates.

No database or secrets: ``auth.ratelimit`` ships an in-memory limiter. Red now because the
module, ``AccountLockedError``, ``LoginAttemptKey``, and the service's throttled-login wiring
do not exist yet.
"""
from __future__ import annotations

import sys
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


SCHEDULER_ROOT = Path(__file__).resolve().parents[1]
if str(SCHEDULER_ROOT) not in sys.path:
    sys.path.insert(0, str(SCHEDULER_ROOT))

SLUG = "maven-demo"
EMAIL = "owner@maven.example"
PASSWORD = "correct horse battery staple"
TENANT_A = "00000000-0000-0000-0000-000000000001"
TENANT_B = "00000000-0000-0000-0000-000000000002"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class F61ThrottleModuleContractTests(unittest.TestCase):
    # spec: F61.ac4 — the limiter exposes a bounded threshold, window, lockout duration, a
    # composite attempt key, and a typed locked error.
    def test_throttle_module_surface(self) -> None:
        from auth.ratelimit import (
            LOCKOUT_DURATION_SECONDS,
            LOCKOUT_WINDOW_SECONDS,
            MAX_FAILED_ATTEMPTS,
            AccountLockedError,
            LoginAttemptKey,
            LoginRateLimiter,
        )

        self.assertGreaterEqual(MAX_FAILED_ATTEMPTS, 3)
        self.assertGreater(LOCKOUT_WINDOW_SECONDS, 0)
        self.assertGreater(LOCKOUT_DURATION_SECONDS, 0)
        self.assertTrue(issubclass(AccountLockedError, Exception))
        key = LoginAttemptKey(tenant_id=TENANT_A, email=EMAIL, ip="203.0.113.7")
        self.assertEqual(TENANT_A, key.tenant_id)
        for method in ("check", "record_failure", "record_success"):
            self.assertTrue(callable(getattr(LoginRateLimiter, method)))


class F61AccountLockoutTests(unittest.TestCase):
    def _key(self, *, tenant_id=TENANT_A, ip="203.0.113.7"):
        from auth.ratelimit import LoginAttemptKey

        return LoginAttemptKey(tenant_id=tenant_id, email=EMAIL, ip=ip)

    # spec: F61.ac4 — the account locks only once failures reach the threshold inside the
    # window; below the threshold a login check still passes.
    def test_locks_at_threshold_not_before(self) -> None:
        from auth.ratelimit import MAX_FAILED_ATTEMPTS, AccountLockedError, LoginRateLimiter

        limiter = LoginRateLimiter()
        key = self._key()
        for _ in range(MAX_FAILED_ATTEMPTS - 1):
            limiter.record_failure(key)
        limiter.check(key)  # still under the threshold — no raise
        limiter.record_failure(key)
        with self.assertRaises(AccountLockedError):
            limiter.check(key)

    # spec: F61.ac4 — a successful login resets the account's failure counter.
    def test_success_resets_counter(self) -> None:
        from auth.ratelimit import MAX_FAILED_ATTEMPTS, LoginRateLimiter

        limiter = LoginRateLimiter()
        key = self._key()
        for _ in range(MAX_FAILED_ATTEMPTS - 1):
            limiter.record_failure(key)
        limiter.record_success(key)
        # Counter reset: the next near-threshold run of failures must not be already locked.
        for _ in range(MAX_FAILED_ATTEMPTS - 1):
            limiter.record_failure(key)
        limiter.check(key)  # no raise — the earlier failures were cleared

    # spec: F61.ac4 — the lock is time-boxed: after the lockout duration elapses, the check
    # passes again (no permanent denial of a victim account).
    def test_lock_expires_after_duration(self) -> None:
        from auth.ratelimit import (
            LOCKOUT_DURATION_SECONDS,
            MAX_FAILED_ATTEMPTS,
            AccountLockedError,
            LoginRateLimiter,
        )

        limiter = LoginRateLimiter()
        key = self._key()
        start = _now()
        for _ in range(MAX_FAILED_ATTEMPTS):
            limiter.record_failure(key, now=start)
        with self.assertRaises(AccountLockedError):
            limiter.check(key, now=start)
        later = start + timedelta(seconds=LOCKOUT_DURATION_SECONDS + 1)
        limiter.check(key, now=later)  # lock has expired

    # spec: F61.ac4 — failures older than the window are not counted toward a lockout.
    def test_window_bounds_counted_failures(self) -> None:
        from auth.ratelimit import (
            LOCKOUT_WINDOW_SECONDS,
            MAX_FAILED_ATTEMPTS,
            LoginRateLimiter,
        )

        limiter = LoginRateLimiter()
        key = self._key()
        old = _now() - timedelta(seconds=LOCKOUT_WINDOW_SECONDS + 5)
        for _ in range(MAX_FAILED_ATTEMPTS):
            limiter.record_failure(key, now=old)
        limiter.check(key, now=_now())  # all failures aged out of the window — no raise


class F61NoCrossTenantDosTests(unittest.TestCase):
    # spec: F61.ac4 — the same email in a different tenant is a different account key, so
    # locking tenant A's account never locks tenant B's same-named account.
    def test_lockout_is_tenant_scoped(self) -> None:
        from auth.ratelimit import MAX_FAILED_ATTEMPTS, AccountLockedError, LoginAttemptKey, LoginRateLimiter

        limiter = LoginRateLimiter()
        a = LoginAttemptKey(tenant_id=TENANT_A, email=EMAIL, ip="203.0.113.7")
        b = LoginAttemptKey(tenant_id=TENANT_B, email=EMAIL, ip="203.0.113.7")
        for _ in range(MAX_FAILED_ATTEMPTS):
            limiter.record_failure(a)
        with self.assertRaises(AccountLockedError):
            limiter.check(a)
        limiter.check(b)  # tenant B's identically-named account is unaffected

    # spec: F61.ac4 — a shared-IP flood against OTHER accounts must not hard-lock a victim
    # account that is itself under its own threshold (no cross-tenant DoS via a NAT IP).
    def test_shared_ip_flood_does_not_lock_an_innocent_account(self) -> None:
        from auth.ratelimit import MAX_FAILED_ATTEMPTS, LoginAttemptKey, LoginRateLimiter

        limiter = LoginRateLimiter()
        shared_ip = "198.51.100.42"
        # Attacker hammers many distinct accounts from one shared IP.
        for n in range(MAX_FAILED_ATTEMPTS * 5):
            limiter.record_failure(LoginAttemptKey(tenant_id=TENANT_A, email=f"victim{n}@x", ip=shared_ip))
        victim = LoginAttemptKey(tenant_id=TENANT_B, email="real-owner@x", ip=shared_ip)
        # The victim's own account has no failures: it must not be locked by the IP flood.
        limiter.check(victim)


class F61ConcurrencyTests(unittest.TestCase):
    # spec: F61.ac4 — concurrent failures are counted atomically (no lost updates): exactly
    # MAX_FAILED_ATTEMPTS simultaneous failures engage the lock.
    def test_concurrent_failures_count_without_lost_updates(self) -> None:
        from auth.ratelimit import MAX_FAILED_ATTEMPTS, AccountLockedError, LoginAttemptKey, LoginRateLimiter

        limiter = LoginRateLimiter()
        key = LoginAttemptKey(tenant_id=TENANT_A, email=EMAIL, ip="203.0.113.7")
        barrier = threading.Barrier(MAX_FAILED_ATTEMPTS)

        def worker() -> None:
            barrier.wait()
            limiter.record_failure(key)

        threads = [threading.Thread(target=worker) for _ in range(MAX_FAILED_ATTEMPTS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        with self.assertRaises(AccountLockedError):
            limiter.check(key)


class F61ThrottledLoginIntegrationTests(unittest.TestCase):
    # spec: F61.ac4 — the login flow consults the limiter: after the threshold of failed
    # logins, a further login is refused WITHOUT verifying the password (even a correct one).
    def test_login_locks_out_after_repeated_failures(self) -> None:
        from auth.ratelimit import MAX_FAILED_ATTEMPTS, AccountLockedError
        from auth.service import AuthenticationError, TenantAuthService

        service = TenantAuthService.for_contract_fixture()
        for _ in range(MAX_FAILED_ATTEMPTS):
            with self.assertRaises(AuthenticationError):
                service.login_json(tenant_slug=SLUG, email=EMAIL, password="wrong", client_ip="203.0.113.7")
        # Even the correct password is refused while locked, and locking is distinguishable.
        with self.assertRaises(AccountLockedError):
            service.login_json(tenant_slug=SLUG, email=EMAIL, password=PASSWORD, client_ip="203.0.113.7")


if __name__ == "__main__":
    unittest.main()
