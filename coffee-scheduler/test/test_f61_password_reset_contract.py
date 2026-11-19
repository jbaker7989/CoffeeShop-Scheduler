"""CS-061b / F61 AC4 password-reset contracts; red before implementation.

A reset is enumeration-safe (a known, an inactive, and an unknown account are externally
identical), single-use, time-boxed, stored only as a peppered one-way digest, and it
invalidates the user's existing sessions on redeem (ties to the AC3 revocation cutoff).

No database or secrets. The in-memory ``TenantAuthService.for_contract_fixture`` captures
minted reset secrets on ``sent_reset_secrets`` (the fixture's stand-in for the delivery
channel), so the test can redeem them. Red now because ``auth.reset`` and the
``request_password_reset`` / ``redeem_password_reset`` seams do not exist yet.
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


SCHEDULER_ROOT = Path(__file__).resolve().parents[1]
if str(SCHEDULER_ROOT) not in sys.path:
    sys.path.insert(0, str(SCHEDULER_ROOT))

SLUG = "maven-demo"
EMAIL = "owner@maven.example"
OLD_PASSWORD = "correct horse battery staple"
NEW_PASSWORD = "a-brand-new-much-longer-passphrase"
LOGIN = {"tenant_slug": SLUG, "email": EMAIL, "password": OLD_PASSWORD}
FIXTURE_TENANT = "00000000-0000-0000-0000-000000000001"
FIXTURE_USER = "00000000-0000-0000-0000-000000000001"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class F61PasswordResetModuleContractTests(unittest.TestCase):
    # spec: F61.ac4 — the reset module exposes a fixed, explicit time-box and a typed error.
    def test_reset_module_surface(self) -> None:
        from auth.reset import PASSWORD_RESET_TTL_SECONDS, PasswordResetError, PasswordResetService

        self.assertIsInstance(PASSWORD_RESET_TTL_SECONDS, int)
        self.assertGreater(PASSWORD_RESET_TTL_SECONDS, 0)
        self.assertLessEqual(PASSWORD_RESET_TTL_SECONDS, 24 * 60 * 60, "reset tokens are short-lived")
        self.assertTrue(issubclass(PasswordResetError, Exception))
        self.assertTrue(callable(PasswordResetService.request_reset))
        self.assertTrue(callable(PasswordResetService.redeem_reset))

    def test_request_reset_computes_the_secret_digest_once(self) -> None:
        from auth.reset import PasswordResetService

        service = PasswordResetService("p" * 32)
        original_digest = service.digest
        digested_secrets = []

        def count_digest(secret: str) -> str:
            digested_secrets.append(secret)
            return original_digest(secret)

        service.digest = count_digest
        secret = service.request_reset("tenant-id", "user-id")

        self.assertEqual([secret], digested_secrets)


class F61PasswordResetEnumerationSafetyTests(unittest.TestCase):
    # spec: F61.ac4 — a known-active, a known-inactive, and an unknown account all return the
    # identical externally observable outcome; existence is never revealed to the caller.
    def test_request_is_enumeration_safe(self) -> None:
        from auth.service import TenantAuthService

        results = []
        for kwargs in ({}, {"user_is_active": False}, {"user_exists": False}):
            service = TenantAuthService.for_contract_fixture(**kwargs)
            results.append(service.request_password_reset(tenant_slug=SLUG, email=EMAIL))
        # No branch raises, and every branch yields the same generic acknowledgement.
        self.assertEqual(1, len(set(repr(r) for r in results)), results)

    # spec: F61.ac4 — a secret is minted and stored only when the account exists; an unknown
    # account mints nothing, yet the caller cannot tell (checked via the fixture capture).
    def test_secret_minted_only_for_real_account(self) -> None:
        from auth.service import TenantAuthService

        real = TenantAuthService.for_contract_fixture()
        real.request_password_reset(tenant_slug=SLUG, email=EMAIL)
        self.assertEqual(1, len(real.sent_reset_secrets))

        unknown = TenantAuthService.for_contract_fixture(user_exists=False)
        unknown.request_password_reset(tenant_slug=SLUG, email=EMAIL)
        self.assertEqual(0, len(unknown.sent_reset_secrets))


class F61PasswordResetRedeemTests(unittest.TestCase):
    def _service_with_outstanding_reset(self):
        from auth.service import TenantAuthService

        service = TenantAuthService.for_contract_fixture()
        service.request_password_reset(tenant_slug=SLUG, email=EMAIL)
        secret = service.sent_reset_secrets[-1]
        return service, secret

    # spec: F61.ac4 — redeeming a valid token sets the new Argon2id credential: the new
    # password logs in, the old one no longer does.
    def test_redeem_sets_new_credential(self) -> None:
        from auth.service import AuthenticationError

        service, secret = self._service_with_outstanding_reset()
        service.redeem_password_reset(secret=secret, new_password=NEW_PASSWORD)
        self.assertTrue(service.login_json(tenant_slug=SLUG, email=EMAIL, password=NEW_PASSWORD))
        with self.assertRaises(AuthenticationError):
            service.login_json(**LOGIN)

    # spec: F61.ac4 — a reset token is single-use: a second redeem is refused.
    def test_reset_token_is_single_use(self) -> None:
        from auth.reset import PasswordResetError

        service, secret = self._service_with_outstanding_reset()
        service.redeem_password_reset(secret=secret, new_password=NEW_PASSWORD)
        with self.assertRaises(PasswordResetError):
            service.redeem_password_reset(secret=secret, new_password="yet-another-passphrase")

    # spec: F61.ac4 — a token past its TTL is refused (time-boxed, explicit expiry).
    def test_expired_reset_token_refused(self) -> None:
        from auth.reset import PASSWORD_RESET_TTL_SECONDS, PasswordResetError

        service, secret = self._service_with_outstanding_reset()
        later = _now() + timedelta(seconds=PASSWORD_RESET_TTL_SECONDS + 1)
        with self.assertRaises(PasswordResetError):
            service.redeem_password_reset(secret=secret, new_password=NEW_PASSWORD, now=later)

    # spec: F61.ac4 — issuing a new reset invalidates any prior outstanding token for the
    # account, so only the newest reset secret can be redeemed.
    def test_new_request_invalidates_prior_token(self) -> None:
        from auth.reset import PasswordResetError

        service, first_secret = self._service_with_outstanding_reset()
        service.request_password_reset(tenant_slug=SLUG, email=EMAIL)
        second_secret = service.sent_reset_secrets[-1]
        self.assertNotEqual(first_secret, second_secret)
        with self.assertRaises(PasswordResetError):
            service.redeem_password_reset(secret=first_secret, new_password=NEW_PASSWORD)
        service.redeem_password_reset(secret=second_secret, new_password=NEW_PASSWORD)  # newest works

    # spec: F61.ac4 — malformed / unknown / oversized reset secrets are refused before work.
    def test_malformed_and_unknown_tokens_refused(self) -> None:
        from auth.reset import PasswordResetError

        service, _ = self._service_with_outstanding_reset()
        for bad in ("", "unknown-reset-token", "x" * 8193):
            with self.assertRaises(PasswordResetError, msg=repr(bad)):
                service.redeem_password_reset(secret=bad, new_password=NEW_PASSWORD)


class F61PasswordResetSessionInvalidationTests(unittest.TestCase):
    # spec: F61.ac4 — redeeming a reset invalidates the user's existing sessions: an access
    # token and a refresh token minted before the reset both stop working afterwards.
    def test_redeem_invalidates_existing_sessions(self) -> None:
        from auth.service import AuthenticationError, SessionRevokedError, TenantAuthService

        service = TenantAuthService.for_contract_fixture()
        issued = service.login_json(**LOGIN)
        service.request_password_reset(tenant_slug=SLUG, email=EMAIL)
        secret = service.sent_reset_secrets[-1]
        service.redeem_password_reset(secret=secret, new_password=NEW_PASSWORD)

        with self.assertRaises(SessionRevokedError):
            service.authorize_access_request(issued.access_token)
        with self.assertRaises(AuthenticationError):
            service.refresh_json(refresh_token=issued.refresh_token)


if __name__ == "__main__":
    unittest.main()
