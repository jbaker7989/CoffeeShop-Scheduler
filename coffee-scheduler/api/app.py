"""Same-origin API gateway for the manager UI, on the CS-061a auth boundary.

BUG-035 cutover: the reachable gateway now mounts the CS-061a auth router (RS256 access
tokens, opaque rotating refresh, JSON vs browser transports) and its JWKS, and authenticates
protected endpoints from *validated, reconciled* CS-061a claims. The legacy CS-030 demo
``POST /auth/login`` (HS256 ``session`` cookie, ``DEMO_MANAGER_*`` credentials) is removed —
exactly one production handler owns each ``/auth/*`` route, and no duplicate ``/auth/login``
exists for Starlette to shadow.

Tenant context comes ONLY from the reconciled access-token claims: a request body, query, or
cookie other than the signed access credential cannot select a tenant. Protected reads then open
the ``app_rw`` RLS transaction with that reconciled ``tenant_id`` (unchanged CS-030 data path).

CS-030 compatibility: the Next.js manager shell still authenticates with the legacy ``session``
cookie; migrating it to the CS-061a browser ``access_token`` cookie is CS-070 (frontend session
UX + CSRF/CORS), tracked separately. This change fixes the backend boundary without weakening any
CS-061a contract.
"""
from __future__ import annotations

from typing import Any, Callable

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status

from auth.api import create_auth_router
from auth.service import AuthenticationError, Principal, TenantAuthService

from .auth_gateway import ACCESS_COOKIE_NAME, build_auth_service_from_env, extract_access_credential


BROWSER_REFRESH_COOKIE = "refresh_token"
BROWSER_REFRESH_PATH = "/auth/browser"

def create_app(
    auth_service: TenantAuthService,
    *,
    fetch_stores: Callable[[str], list[dict]] | None = None,
) -> FastAPI:
    """Build the gateway around a CS-061a ``auth_service``.

    ``fetch_stores`` is injectable so the auth boundary can be tested through an ASGI client
    without a database; production defaults to the RLS-scoped ``api.db.fetch_stores`` (imported
    lazily so the ASGI tests do not require the psycopg connection pool).
    """
    if fetch_stores is None:
        from .db import fetch_stores as fetch_stores  # noqa: PLW0127 — lazy production default

    app = FastAPI()

    # The four CS-061a auth routes + /.well-known/jwks.json. This is the single owner of
    # /auth/login. BrowserCookieConfig remains Secure even in local development: TLS is a
    # deployment prerequisite, not a switch that may weaken bearer-cookie transport.
    app.include_router(create_auth_router(auth_service))

    def current_principal(request: Request) -> Principal:
        # Tenant/user/role are established only from the reconciled access-token claims.
        token = extract_access_credential(request)
        if token is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
        try:
            return auth_service.authenticate_access_token(token)
        except AuthenticationError:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    @app.get("/me")
    def me(principal: Principal = Depends(current_principal)) -> dict[str, Any]:
        # Identity reflects the reconciled claims (CS-061a has no tenant-slug claim; CS-030's
        # slug-shaped /me is superseded by the signed tenant/user/role identity).
        return {
            "tenant_id": principal.tenant_id,
            "user_id": principal.user_id,
            "role": principal.role,
        }

    @app.get("/stores")
    def stores(principal: Principal = Depends(current_principal)) -> dict[str, list[dict]]:
        # tenant_id is the reconciled claim, never request input; fetch_stores opens app_rw RLS.
        return {"stores": fetch_stores(principal.tenant_id)}

    @app.post("/auth/logout")
    def logout(response: Response) -> dict[str, bool]:
        # Clear the CS-061a browser cookies. True server-side refresh revocation is CS-061b; this
        # only drops the client's credentials from the two cookie scopes the browser transport sets.
        response.delete_cookie(ACCESS_COOKIE_NAME, path="/", httponly=True, samesite="strict", secure=True)
        response.delete_cookie(
            BROWSER_REFRESH_COOKIE, path=BROWSER_REFRESH_PATH, httponly=True, samesite="strict", secure=True
        )
        return {"ok": True}

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


# Production app, built lazily on first attribute access (``uvicorn api.app:app``). Deferring the
# build keeps the module importable — so create_app(...) is usable in the ASGI tests — while the
# production ``app`` still fails closed: accessing it without a valid CS-061a auth configuration
# raises GatewayAuthConfigError, never falling back to the demo/SESSION_SECRET boundary.
_production_app: FastAPI | None = None


def __getattr__(name: str) -> Any:
    if name == "app":
        global _production_app
        if _production_app is None:
            _production_app = create_app(build_auth_service_from_env())
        return _production_app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
