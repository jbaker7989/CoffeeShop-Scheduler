"""Configuration for the manager UI gateway.

The demo password may be supplied as an Argon2id hash.  When it is not supplied,
the gateway hashes ``DEMO_MANAGER_PASSWORD`` during startup so a local demo works
without committing a password hash to the repository.
"""
from __future__ import annotations

import os

from argon2 import PasswordHasher


GATEWAY_DATABASE_URL = os.environ.get(
    "GATEWAY_DATABASE_URL",
    "postgresql://coffeeshop:coffeeshop@localhost:6432/coffeeshop",
)

SESSION_SECRET = os.environ.get("SESSION_SECRET")
if not SESSION_SECRET or len(SESSION_SECRET) < 32:
    raise RuntimeError("SESSION_SECRET must be set and at least 32 characters long")

COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "false").lower() == "true"

try:
    JWT_TTL_SECONDS = int(os.environ.get("JWT_TTL_SECONDS", "3600"))
except ValueError as exc:
    raise RuntimeError("JWT_TTL_SECONDS must be a positive integer") from exc
if JWT_TTL_SECONDS <= 0:
    raise RuntimeError("JWT_TTL_SECONDS must be a positive integer")

DEMO_TENANT_SLUG = os.environ.get("DEMO_TENANT_SLUG", "maven-demo")
DEMO_MANAGER_EMAIL = os.environ.get("DEMO_MANAGER_EMAIL", "manager@maven-demo.coffee")
DEMO_MANAGER_PASSWORD_HASH = os.environ.get("DEMO_MANAGER_PASSWORD_HASH")
if not DEMO_MANAGER_PASSWORD_HASH:
    DEMO_MANAGER_PASSWORD_HASH = PasswordHasher().hash(
        os.environ.get("DEMO_MANAGER_PASSWORD", "demo-pass-change-me")
    )
