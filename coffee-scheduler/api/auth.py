"""Password, JWT, and cookie helpers for the gateway."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Response

from .config import COOKIE_SECURE, JWT_TTL_SECONDS, SESSION_SECRET


_password_hasher = PasswordHasher()
_JWT_ALGORITHM = "HS256"
_SESSION_COOKIE = "session"


def hash_password(password: str) -> str:
    """Create an Argon2id password hash."""
    return _password_hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """Fail closed when a supplied Argon2id hash cannot be verified."""
    try:
        return _password_hasher.verify(password_hash, password)
    except (InvalidHashError, VerificationError):
        return False


def issue_token(claims: dict[str, Any]) -> str:
    """Sign claims with a bounded, UTC expiry."""
    payload = dict(claims)
    payload["exp"] = datetime.now(timezone.utc) + timedelta(seconds=JWT_TTL_SECONDS)
    return jwt.encode(payload, SESSION_SECRET, algorithm=_JWT_ALGORITHM)


def verify_token(token: str) -> dict[str, Any] | None:
    """Return verified claims, never exposing JWT failures to callers."""
    try:
        claims = jwt.decode(
            token,
            SESSION_SECRET,
            algorithms=[_JWT_ALGORITHM],
            options={"require": ["exp"]},
        )
    except (jwt.PyJWTError, TypeError, ValueError):
        return None
    return claims if isinstance(claims, dict) else None


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=_SESSION_COOKIE,
        value=token,
        max_age=JWT_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=COOKIE_SECURE,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=_SESSION_COOKIE,
        httponly=True,
        samesite="lax",
        secure=COOKIE_SECURE,
        path="/",
    )
