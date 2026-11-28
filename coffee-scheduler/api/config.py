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

from .routing import TenantRoutingConfig

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


def gateway_cors_origins_from_env() -> list[str]:
    """Read the explicit gateway origin allowlist when an app is built."""
    origins = [
        origin for value in os.environ.get("GATEWAY_CORS_ALLOWED_ORIGINS", "").split(",")
        if (origin := value.strip())
    ]
    if "*" in origins or "null" in origins:
        raise ValueError("GATEWAY_CORS_ALLOWED_ORIGINS requires explicit origins")
    return origins


def tenant_routing_config_from_env() -> TenantRoutingConfig | None:
    """Build the CS-070 edge configuration, failing before the gateway starts.

    **Opt-in** (SDD AC3.4): tenant routing installs only when ``TENANT_ROUTING_ENABLED=true``.
    Until the manager UI adopts the ``/t/<slug>/`` dev topology (and prod subdomains), the
    default is the CS-030/CS-061 single-tenant same-origin behavior — otherwise the routing
    middleware would 404 the shell's unprefixed ``/me`` / ``/stores`` / ``/auth/login`` calls
    (BUG-057). When enabled, the full topology is validated fail-fast at startup.

    Kept as a function so ASGI/unit-test imports can supply an explicit topology without
    process-global environment mutation. The production app invokes it in ``api.app.__getattr__``.
    """
    if os.environ.get("TENANT_ROUTING_ENABLED", "").lower() != "true":
        return None
    return TenantRoutingConfig.from_environment()
