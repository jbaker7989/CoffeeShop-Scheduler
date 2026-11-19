"""Test-only helpers for constructing adversarial authentication credentials."""
from __future__ import annotations

import uuid
from typing import Mapping

from auth.repository import InMemoryAuthRepository


def issue_contract_token(
    service,
    *,
    tenant_id: str | None = None,
    user_id: str | None = None,
    role: str = "owner",
    claim_overrides: Mapping | None = None,
) -> str:
    """Sign a deliberately controlled claim set for authentication contract tests only."""
    user_id = str(user_id or InMemoryAuthRepository._FIXTURE_USER_ID)
    tenant_id = str(tenant_id or InMemoryAuthRepository._FIXTURE_TENANT_ID)
    token_service = service._tokens
    issued_at = int(service._now().timestamp())
    claims = {
        "iss": token_service.issuer,
        "aud": token_service.audience,
        "sub": user_id,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "role": role,
        "is_saas_admin": False,
        "jti": str(uuid.uuid4()),
        "iat": issued_at,
        "nbf": issued_at,
        "exp": issued_at + token_service.ttl_seconds,
    }
    if claim_overrides:
        claims.update(claim_overrides)
    return token_service.sign_unchecked(claims)


def _fixture_repository(service) -> InMemoryAuthRepository:
    repository = service._repo
    if not isinstance(repository, InMemoryAuthRepository):
        raise TypeError("auth fixture mutation requires InMemoryAuthRepository")
    return repository


def set_fixture_tenant_status(service, status: str) -> None:
    repository = _fixture_repository(service)
    repository.set_tenant_status(repository._FIXTURE_TENANT_ID, status)


def set_fixture_user_active(service, active: bool) -> None:
    repository = _fixture_repository(service)
    repository.set_user_active(
        repository._FIXTURE_TENANT_ID,
        repository._FIXTURE_USER_ID,
        active,
    )
