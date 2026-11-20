"""CS-062 signup, email verification, and first provisioning policy.

The repository object is the existing dedicated ``auth_svc`` seam.  It exposes only
``onboarding_*`` SECURITY DEFINER calls in production; this module never writes
``tenants`` or ``tenant_users`` directly.
"""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Protocol

from auth.repository import OnboardingConflictError, OnboardingProvisionError
from auth.service import Principal, TenantAuthService
from auth.tokens import MAX_REFRESH_TOKEN_BYTES

from .routing import RESERVED_SLUGS, _SLUG


class OnboardingError(Exception):
    pass


class InvalidSignupError(OnboardingError):
    pass


class VerificationError(OnboardingError):
    pass


class EmailNotVerifiedError(OnboardingError):
    pass


class ProvisioningError(OnboardingError):
    pass


class OnboardingRepository(Protocol):
    def onboarding_signup(self, slug: str, name: str, jurisdiction: str, timezone_name: str,
                          email: str, password_hash: str, trial_days: int, when: datetime) -> tuple[str, str]: ...
    def onboarding_issue_verification(self, tenant_id: str, user_id: str, digest: str, expires_at: datetime) -> None: ...
    def onboarding_verify_email(self, digest: str, when: datetime) -> tuple[str, str] | None: ...
    def onboarding_email_verified(self, tenant_id: str, user_id: str) -> bool: ...
    def onboarding_tenant_jurisdiction(self, tenant_id: str) -> str | None: ...
    def onboarding_suspend_expired_trials(self, when: datetime) -> int: ...
    def onboarding_provision(self, tenant_id: str, jurisdiction: str, when: datetime) -> bool: ...


@dataclass(frozen=True)
class SignupResult:
    tenant_id: str
    user_id: str
    access_token: str
    refresh_token: str
    expires_in: int
    verify_url: str


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _positive_env_int(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        raise RuntimeError(f"{name} must be a positive integer") from None
    if value <= 0:
        raise RuntimeError(f"{name} must be a positive integer")
    return value


class OnboardingService:
    """Policy layer for CS-062.  Dev notification is intentionally a one-time URL."""

    def __init__(self, auth_service: TenantAuthService, repository: OnboardingRepository | None = None,
                 *, trial_days: int | None = None, verification_ttl_seconds: int | None = None,
                 now: Callable[[], datetime] = _utcnow) -> None:
        self._auth = auth_service
        self._repo = repository if repository is not None else auth_service._repo  # same trusted service seam
        self._trial_days = trial_days if trial_days is not None else _positive_env_int("TRIAL_DAYS", 14)
        self._verification_ttl = verification_ttl_seconds if verification_ttl_seconds is not None else _positive_env_int("VERIFICATION_TTL", 3600)
        self._now = now
        if self._trial_days <= 0 or self._verification_ttl <= 0:
            raise ValueError("onboarding lifetimes must be positive")

    @staticmethod
    def _validate_signup(slug: str, name: str, jurisdiction: str, email: str, password: str) -> None:
        if not isinstance(slug, str) or not _SLUG.fullmatch(slug.lower()):
            raise InvalidSignupError("invalid slug")
        if slug.lower() in RESERVED_SLUGS:
            raise InvalidSignupError("reserved slug")
        if not all(isinstance(value, str) and value.strip() for value in (name, jurisdiction, email, password)):
            raise InvalidSignupError("invalid signup")
        if len(password) > 1024 or len(email) > 320 or len(name) > 255 or len(jurisdiction) > 128:
            raise InvalidSignupError("invalid signup")

    def _issue_verification(self, tenant_id: str, user_id: str, email: str) -> str:
        token = secrets.token_urlsafe(32)
        # Reuse the existing keyed HMAC token primitive.  No plaintext is persisted.
        digest = self._auth._refresh.digest(token)
        self._repo.onboarding_issue_verification(tenant_id, user_id, digest, self._now() + timedelta(seconds=self._verification_ttl))
        return f"/verify-email?token={token}"

    def signup(self, *, slug: str, name: str, jurisdiction: str, email: str, password: str,
               timezone_name: str = "America/New_York") -> SignupResult:
        self._validate_signup(slug, name, jurisdiction, email, password)
        now = self._now()
        try:
            tenant_id, user_id = self._repo.onboarding_signup(
                slug.lower(), name.strip(), jurisdiction.strip(), timezone_name, email.strip(),
                self._auth._passwords.hash(password), self._trial_days, now,
            )
        except OnboardingConflictError:
            raise
        verify_url = self._issue_verification(tenant_id, user_id, email)
        issued = self._auth._issue(tenant_id=tenant_id, user_id=user_id, role="owner", now=now)
        return SignupResult(tenant_id, user_id, issued.access_token, issued.refresh_token, issued.expires_in, verify_url)

    def verify_email(self, token: str) -> None:
        if not isinstance(token, str) or not token or len(token) > MAX_REFRESH_TOKEN_BYTES:
            raise VerificationError("verification failed")
        result = self._repo.onboarding_verify_email(self._auth._refresh.digest(token), self._now())
        if result is None:
            # Unknown, expired, and already-consumed intentionally share one response.
            raise VerificationError("verification failed")

    def resend_verification(self, principal: Principal) -> str:
        if principal.role != "owner":
            raise OnboardingError("forbidden")
        # The token endpoint is scoped only by reconciled principal; email is not a request input.
        return self._issue_verification(principal.tenant_id, principal.user_id, "owner")

    def is_email_verified(self, principal: Principal) -> bool:
        return bool(self._repo.onboarding_email_verified(principal.tenant_id, principal.user_id))

    def provision(self, principal: Principal) -> bool:
        if principal.role != "owner":
            raise OnboardingError("forbidden")
        if not self.is_email_verified(principal):
            raise EmailNotVerifiedError("email not verified")
        jurisdiction = self._repo.onboarding_tenant_jurisdiction(principal.tenant_id)
        if not jurisdiction:
            raise ProvisioningError("provisioning unavailable")
        try:
            return self._repo.onboarding_provision(principal.tenant_id, jurisdiction, self._now())
        except OnboardingProvisionError as exc:
            raise ProvisioningError("jurisdiction has no verified rule set") from exc
