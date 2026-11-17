"""CS-061b / F61 AC4 optional TOTP MFA contracts; red before implementation.

TOTP MFA is available only to ``owner``/``manager``. The secret is encrypted at rest and
never returned after enrolment; verification tolerates a bounded clock skew, rejects replay
of an already-used time-step, and recovery codes are single-use. With MFA enabled, a correct
password alone never yields tokens (bypass resistance).

No database or secrets: ``auth.mfa`` provides the primitives and the in-memory fixture wires
them. Red now because ``auth.mfa`` and the service's MFA enrolment/login seams do not exist.
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
PASSWORD = "correct horse battery staple"
FIXTURE_TENANT = "00000000-0000-0000-0000-000000000001"
FIXTURE_USER = "00000000-0000-0000-0000-000000000001"
FIXED = datetime(2026, 11, 16, 12, 0, 0, tzinfo=timezone.utc)
MFA_ENV = {"AUTH_MFA_ENC_KEY": "u" * 44}  # a sufficiently long encryption key for the fixture


class F61MfaModuleContractTests(unittest.TestCase):
    # spec: F61.ac4 — the MFA module exposes a fixed period/window and the typed errors that
    # distinguish "MFA needed", replay, and an illegal enrolment.
    def test_mfa_module_surface(self) -> None:
        from auth.mfa import (
            TOTP_PERIOD_SECONDS,
            TOTP_WINDOW_STEPS,
            MfaEnrollmentError,
            MfaReplayError,
            MfaRequiredError,
            MfaSecretCipher,
            RecoveryCodeService,
            TOTPService,
        )

        self.assertEqual(30, TOTP_PERIOD_SECONDS)
        self.assertGreaterEqual(TOTP_WINDOW_STEPS, 1)
        self.assertLessEqual(TOTP_WINDOW_STEPS, 2, "a wide window weakens TOTP")
        for err in (MfaRequiredError, MfaReplayError, MfaEnrollmentError):
            self.assertTrue(issubclass(err, Exception))
        for svc in (TOTPService, MfaSecretCipher, RecoveryCodeService):
            self.assertTrue(callable(svc))


class F61MfaSecretAtRestTests(unittest.TestCase):
    # spec: F61.ac4 — the TOTP secret is encrypted at rest and round-trips; the ciphertext is
    # not the plaintext, and a missing/short key is a hard failure (no plaintext fallback).
    def test_secret_cipher_roundtrip_and_key_required(self) -> None:
        from auth.mfa import MfaSecretCipher

        cipher = MfaSecretCipher.from_environment(MFA_ENV)
        secret = b"JBSWY3DPEHPK3PXP"
        ciphertext = cipher.encrypt(secret)
        self.assertIsInstance(ciphertext, (bytes, bytearray))
        self.assertNotIn(secret, bytes(ciphertext))
        self.assertEqual(secret, cipher.decrypt(ciphertext))
        with self.assertRaises(Exception):
            MfaSecretCipher.from_environment({"AUTH_MFA_ENC_KEY": ""})


class F61TotpVerificationTests(unittest.TestCase):
    # spec: F61.ac4 — a code within the allowed window verifies; a code outside the window
    # (too old / too new) is refused.
    def test_totp_window_tolerance(self) -> None:
        from auth.mfa import TOTP_PERIOD_SECONDS, TOTP_WINDOW_STEPS, TOTPService

        totp = TOTPService()
        secret = totp.generate_secret()
        code = totp.code_at(secret, now=FIXED)
        self.assertTrue(totp.verify(secret, code, now=FIXED))
        # One step inside the window still verifies.
        in_window = FIXED + timedelta(seconds=TOTP_PERIOD_SECONDS * TOTP_WINDOW_STEPS)
        self.assertTrue(totp.verify(secret, code, now=in_window))
        # Well outside the window is refused.
        out_window = FIXED + timedelta(seconds=TOTP_PERIOD_SECONDS * (TOTP_WINDOW_STEPS + 3))
        self.assertFalse(totp.verify(secret, code, now=out_window))

    # spec: F61.ac4 — a used time-step cannot be replayed: once a code's step is consumed,
    # re-presenting a code from that step (or earlier) is rejected.
    def test_totp_replay_rejected_by_monotonic_timestep(self) -> None:
        from auth.mfa import MfaReplayError, TOTPService

        totp = TOTPService()
        secret = totp.generate_secret()
        code = totp.code_at(secret, now=FIXED)
        consumed_step = totp.consume(secret, code, now=FIXED, last_timestep=None)
        self.assertIsInstance(consumed_step, int)
        with self.assertRaises(MfaReplayError):
            totp.consume(secret, code, now=FIXED, last_timestep=consumed_step)


class F61RecoveryCodeTests(unittest.TestCase):
    # spec: F61.ac4 — recovery codes are single-use: a code verifies once, then never again.
    def test_recovery_codes_are_single_use(self) -> None:
        from auth.mfa import RecoveryCodeService

        service = RecoveryCodeService()
        codes = service.generate()
        self.assertGreaterEqual(len(codes), 5)
        digests = [service.digest(code) for code in codes]
        # Stored as digests, never the raw code.
        self.assertNotIn(codes[0], digests)
        remaining = service.consume(codes[0], digests)
        self.assertEqual(len(digests) - 1, len(remaining), "the used code's digest is removed")
        self.assertFalse(service.try_consume(codes[0], remaining), "a consumed code cannot be reused")


class F61MfaEnrollmentAndLoginTests(unittest.TestCase):
    def _enroll(self, role: str = "owner"):
        from auth.service import TenantAuthService

        service = TenantAuthService.for_contract_fixture(role=role, mfa_env=MFA_ENV)
        enrollment = service.begin_mfa_enrollment(tenant_id=FIXTURE_TENANT, user_id=FIXTURE_USER)
        from auth.mfa import TOTPService

        code = TOTPService().code_at(enrollment.secret, now=FIXED)
        service.confirm_mfa_enrollment(
            tenant_id=FIXTURE_TENANT, user_id=FIXTURE_USER, code=code, now=FIXED
        )
        return service, enrollment.secret

    # spec: F61.ac4 — only owner/manager may enrol MFA; staff/read_only are refused.
    def test_only_owner_manager_can_enroll(self) -> None:
        from auth.service import TenantAuthService
        from auth.mfa import MfaEnrollmentError

        for role in ("staff", "read_only"):
            service = TenantAuthService.for_contract_fixture(role=role, mfa_env=MFA_ENV)
            with self.assertRaises(MfaEnrollmentError, msg=role):
                service.begin_mfa_enrollment(tenant_id=FIXTURE_TENANT, user_id=FIXTURE_USER)

    # spec: F61.ac4 — with MFA enabled, a correct password alone yields no tokens: login
    # raises MfaRequiredError and only a valid TOTP code completes it (bypass resistance).
    def test_password_alone_does_not_bypass_mfa(self) -> None:
        from auth.mfa import MfaRequiredError, TOTPService
        from auth.service import AuthenticationError

        service, secret = self._enroll()
        with self.assertRaises(MfaRequiredError):
            service.login_json(tenant_slug=SLUG, email=EMAIL, password=PASSWORD, now=FIXED)
        with self.assertRaises(AuthenticationError):
            service.login_json(tenant_slug=SLUG, email=EMAIL, password=PASSWORD, mfa_code="000000", now=FIXED)
        valid = TOTPService().code_at(secret, now=FIXED)
        issued = service.login_json(tenant_slug=SLUG, email=EMAIL, password=PASSWORD, mfa_code=valid, now=FIXED)
        self.assertTrue(issued.access_token)

    # spec: F61.ac4 — a single-use recovery code also completes login, and is then spent.
    def test_recovery_code_completes_login_once(self) -> None:
        from auth.service import AuthenticationError

        service, _ = self._enroll()
        recovery = service.mfa_recovery_codes  # fixture capture of the codes issued at confirm
        issued = service.login_json(
            tenant_slug=SLUG, email=EMAIL, password=PASSWORD, mfa_code=recovery[0], now=FIXED
        )
        self.assertTrue(issued.access_token)
        # The same recovery code cannot be used a second time.
        with self.assertRaises(AuthenticationError):
            service.login_json(
                tenant_slug=SLUG, email=EMAIL, password=PASSWORD, mfa_code=recovery[0], now=FIXED
            )


if __name__ == "__main__":
    unittest.main()
