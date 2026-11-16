"""Configuration for the manager UI gateway.

The production auth boundary is CS-061a (see ``auth_gateway.py``): RS256 access tokens,
Argon2id credentials, and a dedicated ``AUTH_DATABASE_URL`` principal, all fail-closed.
The settings below are the non-auth gateway knobs plus the retired CS-030 demo-login
helpers. The demo login is NO LONGER mounted in the production app (BUG-035): the
``SESSION_SECRET`` / ``DEMO_MANAGER_*`` values are optional legacy configuration used only
by the dormant ``api.auth`` helper, never a fallback for the real auth boundary.
"""
from __future__ import annotations

import os


GATEWAY_DATABASE_URL = os.environ.get(
    "GATEWAY_DATABASE_URL",
    "postgresql://coffeeshop:coffeeshop@localhost:6432/coffeeshop",
)

COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "false").lower() == "true"

# --- retired CS-030 demo-login helpers (optional; never a production auth fallback) ---
# These are read by the dormant api.auth module only. They intentionally do NOT raise when
# unset: the production gateway no longer serves the demo login, so a missing SESSION_SECRET
# must not block startup of the CS-061a boundary. A deployment that still wants the local
# demo helper supplies a >=32-char SESSION_SECRET itself.
SESSION_SECRET = os.environ.get("SESSION_SECRET")

try:
    JWT_TTL_SECONDS = int(os.environ.get("JWT_TTL_SECONDS", "3600"))
except ValueError:
    JWT_TTL_SECONDS = 3600

DEMO_TENANT_SLUG = os.environ.get("DEMO_TENANT_SLUG", "maven-demo")
DEMO_MANAGER_EMAIL = os.environ.get("DEMO_MANAGER_EMAIL", "manager@maven-demo.coffee")
DEMO_MANAGER_PASSWORD_HASH = os.environ.get("DEMO_MANAGER_PASSWORD_HASH")
