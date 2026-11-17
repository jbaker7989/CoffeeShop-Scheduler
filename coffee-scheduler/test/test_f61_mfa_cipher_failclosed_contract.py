"""CS-061b regression: an undecryptable stored MFA secret must fail CLOSED as the single
opaque authentication failure — never an uncaught exception (HTTP 500) and never a status
that reveals MFA-enabled state (BUG-039).

A stored ``mfa_secret_ciphertext`` can become undecryptable through an ordinary operational
event — rotating ``AUTH_MFA_ENC_KEY`` — or through at-rest corruption/tampering. The
AES-GCM envelope then raises ``ValueError('invalid MFA ciphertext')`` inside
``TenantAuthService.login_json``. The SDD requires (security invariants §"Fail-closed
everywhere" and §"One opaque failure") that such a refusal reach the caller as the generic
:class:`AuthenticationError`, indistinguishable from an ordinary credential failure — not as
a raw ``ValueError`` that surfaces as a 500 and leaks, via the status difference, that the
account has MFA enabled.

These tests are DB-free (in-memory contract fixture) and are RED against the current
candidate, which only catches ``MfaReplayError`` around the decrypt/consume block.
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEDULER_ROOT = Path(__file__).resolve().parents[1]
if str(SCHEDULER_ROOT) not in sys.path:
    sys.path.insert(0, str(SCHEDULER_ROOT))

PASSWORD = "correct horse battery staple"
_T0 = datetime(2026, 11, 16, 12, 0, 0, tzinfo=timezone.utc)


def _enrolled_service(enc_key: str):
    """A fully in-memory owner service with MFA enrolled under ``enc_key`` at _T0."""
    from auth.service import TenantAuthService
    from auth.mfa import TOTPService

    svc = TenantAuthService.for_contract_fixture(mfa_env={"AUTH_MFA_ENC_KEY": enc_key})
    repo = svc._repo
    enr = svc.begin_mfa_enrollment(tenant_id=repo._FIXTURE_TENANT_ID, user_id=repo._FIXTURE_USER_ID)
    svc.confirm_mfa_enrollment(
        tenant_id=repo._FIXTURE_TENANT_ID,
        user_id=repo._FIXTURE_USER_ID,
        code=TOTPService().code_at(enr.secret, now=_T0),
        now=_T0,
    )
    return svc, repo, enr.secret


def _rotate_key(svc, new_enc_key: str):
    """Return a new service sharing ``svc``'s repo/state but with a rotated MFA key, as a
    redeploy with a changed ``AUTH_MFA_ENC_KEY`` would produce."""
    from auth.service import TenantAuthService
    from auth.mfa import MfaSecretCipher

    return TenantAuthService(
        repository=svc._repo,
        password_service=svc._passwords,
        token_service=svc._tokens,
        refresh_token_service=svc._refresh,
        mfa_cipher=MfaSecretCipher.from_environment({"AUTH_MFA_ENC_KEY": new_enc_key}),
    )


class MfaCipherFailClosedContract(unittest.TestCase):
    # spec: F61.ac4 — rotating AUTH_MFA_ENC_KEY must not turn MFA logins into 500s; the
    # undecryptable secret maps to the generic opaque AuthenticationError.
    def test_key_rotation_yields_opaque_auth_failure(self) -> None:
        from auth.mfa import TOTPService
        from auth.service import AuthenticationError

        svc, repo, secret = _enrolled_service("A" * 44)
        rotated = _rotate_key(svc, "B" * 44)
        login_time = _T0 + timedelta(seconds=60)
        code = TOTPService().code_at(secret, now=login_time)
        with self.assertRaises(AuthenticationError):
            rotated.login_json(
                tenant_slug=repo._FIXTURE_TENANT_SLUG,
                email=repo._FIXTURE_EMAIL,
                password=PASSWORD,
                mfa_code=code,
                now=login_time,
            )

    # spec: F61.ac4 — a corrupt/tampered ciphertext at rest is refused identically (no
    # uncaught exception, no MFA-state oracle via a differing status).
    def test_corrupt_ciphertext_yields_opaque_auth_failure(self) -> None:
        from auth.service import AuthenticationError

        svc, repo, secret = _enrolled_service("A" * 44)
        # Corrupt the stored ciphertext in place (version prefix preserved, body garbled).
        state = repo._mfa[(repo._FIXTURE_TENANT_ID, repo._FIXTURE_USER_ID)]
        state["ciphertext"] = b"M1" + b"\x00" * 40
        login_time = _T0 + timedelta(seconds=60)
        with self.assertRaises(AuthenticationError):
            svc.login_json(
                tenant_slug=repo._FIXTURE_TENANT_SLUG,
                email=repo._FIXTURE_EMAIL,
                password=PASSWORD,
                mfa_code="000000",
                now=login_time,
            )


if __name__ == "__main__":
    unittest.main()
