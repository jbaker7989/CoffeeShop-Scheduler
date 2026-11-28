"""Same-origin API gateway for the manager UI, on the CS-061a auth boundary.

BUG-035 cutover: the reachable gateway now mounts the CS-061a auth router (RS256 access
tokens, opaque rotating refresh, JSON vs browser transports) and its JWKS, and authenticates
protected endpoints from *validated, reconciled* CS-061a claims. The legacy CS-030 demo
``POST /auth/login`` (HS256 ``session`` cookie, ``DEMO_MANAGER_*`` credentials) is removed —
exactly one production handler owns each ``/auth/*`` route, and no duplicate ``/auth/login``
exists for Starlette to shadow.

CS-070 resolves tenant context from the validated public authority/path before authentication;
the reconciled access-token tenant must then match that immutable route.  Request body, query,
cookie, forwarding, or JWT claims cannot select or override the routed tenant.  Protected reads
then open an RLS transaction with that resolved-and-bound identity.

CS-030 compatibility: the Next.js manager shell still authenticates with the legacy ``session``
cookie; migrating it to the CS-061a browser ``access_token`` cookie is CS-070 (frontend session
UX + CSRF/CORS), tracked separately. This change fixes the backend boundary without weakening any
CS-061a contract.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import asynccontextmanager, suppress
from datetime import UTC, date, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field

from agents.explanation.grounding import ShiftNotFound
from agents.explanation.model import AnthropicExplainer
from agents.explanation.service import Explanation, explain_assignment
from auth.api import BrowserCookieConfig, _set_browser_cookies, create_auth_router
from auth.ratelimit import LoginAttemptKey
from auth.repository import OnboardingConflictError
from auth.service import (
    AuthenticationError,
    InvitationError,
    InvitationPermissionError,
    Principal,
    TenantAuthService,
    TenantSuspendedError,
)

from .auth_gateway import ACCESS_COOKIE_NAME, build_auth_service_from_env, extract_access_credential
from .config import gateway_cors_origins_from_env
from .onboarding import (
    EmailNotVerifiedError,
    InvalidSignupError,
    OnboardingError,
    OnboardingService,
    ProvisioningError,
    VerificationError,
)
from .routing import TenantResolver, TenantRoutingConfig, TenantRoutingMiddleware

BROWSER_REFRESH_COOKIE = "refresh_token"
BROWSER_REFRESH_PATH = "/auth/browser"

class InvitationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=320)
    role: str = Field(min_length=1, max_length=32)
class InvitationAcceptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=1, max_length=4096)
    password: str = Field(min_length=1, max_length=1024)
    display_name: str | None = Field(default=None, max_length=255)


class SignupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slug: str = Field(min_length=1, max_length=63)
    name: str = Field(min_length=1, max_length=255)
    jurisdiction: str = Field(min_length=1, max_length=128)
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=1024)
    timezone: str = Field(default="America/New_York", min_length=1, max_length=128)


class VerifyEmailRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=1, max_length=4096)

def create_app(
    auth_service: TenantAuthService,
    *,
    fetch_stores: Callable[[str], list[dict]] | None = None,
    routing_config: TenantRoutingConfig | None = None,
    tenant_lookup: Callable[[str], str | None] | None = None,
    onboarding_service: OnboardingService | None = None,
) -> FastAPI:
    """Build the gateway with app-owned database accessors or caller-supplied functions."""
    # BUG-031: api.db is imported only when the app must own the pool, so callers that inject
    # fetch_stores/tenant_lookup can build the gateway without a database module present.
    database = None
    if fetch_stores is None or (routing_config is not None and tenant_lookup is None):
        from .db import GatewayDatabase

        database = GatewayDatabase()
        if fetch_stores is None:
            fetch_stores = database.fetch_stores
        if tenant_lookup is None:
            tenant_lookup = database.resolve_active_slug

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            yield
        finally:
            if database is not None:
                database.close()

    app = FastAPI(lifespan=lifespan)
    if routing_config is not None:
        app.add_middleware(
            TenantRoutingMiddleware,
            resolver=TenantResolver(routing_config, tenant_lookup),
            config=routing_config,
        )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=gateway_cors_origins_from_env(),
        allow_methods=["DELETE", "GET", "POST"],
        allow_headers=["Authorization"],
    )
    onboarding = onboarding_service or OnboardingService(auth_service)

    # The four CS-061a auth routes + /.well-known/jwks.json. This is the single owner of
    # /auth/login. BrowserCookieConfig remains Secure even in local development: TLS is a
    # deployment prerequisite, not a switch that may weaken bearer-cookie transport.
    app.include_router(create_auth_router(auth_service))

    @app.middleware("http")
    async def prevent_tenant_response_caching(request: Request, call_next):
        """Never let an intermediary replay an identity or tenant-scoped response.

        The routing middleware already puts these headers on responses it terminates
        before endpoint dispatch.  This covers normal endpoint and HTTPException
        responses as well, including the same-origin deployment where routing is off.
        """
        response = await call_next(request)
        response.headers.setdefault("Cache-Control", "no-store")
        if routing_config is not None:
            vary = response.headers.get("Vary")
            if vary is None:
                response.headers["Vary"] = "Host"
            elif "host" not in {value.strip().lower() for value in vary.split(",")}:
                response.headers["Vary"] = f"{vary}, Host"
        return response

    def current_principal(request: Request) -> Principal:
        # Tenant/user/role are established only from the reconciled access-token claims.
        token = extract_access_credential(request)
        if token is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
        try:
            principal = auth_service.authorize_access_request(token)
        except TenantSuspendedError:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden") from None
        except AuthenticationError:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized") from None
        route = request.scope.get("tenant_route")
        if route is not None and principal.tenant_id != route.tenant_id:
            # Match the signed principal to the route before any endpoint opens its RLS session.
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
        return principal

    def require_role(*roles: str):
        def dependency(principal: Principal = Depends(current_principal)) -> Principal:
            if principal.role not in roles:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
            return principal
        return dependency

    def require_email_verified(principal: Principal = Depends(current_principal)) -> Principal:
        """Reusable F62.AC2 gate for future solver/schedule routes as well as provision."""
        if not onboarding.is_email_verified(principal):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="email not verified")
        return principal

    @app.post("/signup", status_code=status.HTTP_201_CREATED)
    def signup(body: SignupRequest) -> dict[str, Any]:
        try:
            issued = onboarding.signup(
                slug=body.slug, name=body.name, jurisdiction=body.jurisdiction,
                email=body.email, password=body.password, timezone_name=body.timezone,
            )
        except InvalidSignupError as exc:
            # Reserved and malformed labels are rejected before any DB operation.
            raise HTTPException(status_code=422, detail=str(exc)) from None
        except OnboardingConflictError:
            raise HTTPException(status_code=409, detail="slug unavailable") from None
        return {
            "tenant_id": issued.tenant_id, "user_id": issued.user_id,
            "access_token": issued.access_token, "refresh_token": issued.refresh_token,
            "token_type": "bearer", "expires_in": issued.expires_in,
            "verify_url": issued.verify_url,
        }

    @app.post("/verify-email")
    def verify_email(body: VerifyEmailRequest) -> dict[str, bool]:
        try:
            onboarding.verify_email(body.token)
        except VerificationError:
            raise HTTPException(status_code=422, detail="verification failed") from None
        return {"ok": True}

    @app.post("/onboarding/resend-verification")
    def resend_verification(principal: Principal = Depends(require_role("owner"))) -> dict[str, str]:
        try:
            return {"verify_url": onboarding.resend_verification(principal)}
        except OnboardingError:
            raise HTTPException(status_code=403, detail="Forbidden") from None

    @app.post("/onboarding/provision")
    def provision(principal: Principal = Depends(require_email_verified)) -> dict[str, bool]:
        if principal.role != "owner":
            raise HTTPException(status_code=403, detail="Forbidden")
        try:
            return {"provisioned": onboarding.provision(principal)}
        except EmailNotVerifiedError:
            raise HTTPException(status_code=403, detail="email not verified") from None
        except ProvisioningError:
            raise HTTPException(status_code=422, detail="jurisdiction has no verified rule set") from None

    @app.post("/invitations")
    def create_invitation(
        body: InvitationCreateRequest,
        principal: Principal = Depends(require_role("owner", "manager")),
    ) -> dict[str, Any]:
        try:
            issued = auth_service.create_invitation(
                principal,
                body.email,
                body.role,
                accept_url_base="/invitations/accept",
            )
        except InvitationPermissionError:
            raise HTTPException(status_code=403, detail="Forbidden") from None
        except InvitationError:
            raise HTTPException(status_code=409, detail="invitation unavailable") from None
        return {
            "invitation_id": issued.invitation_id,
            "expires_at": issued.expires_at,
            "accept_url": f"/invitations/accept?token={issued.token}",
            "token": issued.token,
        }

    @app.get("/invitations")
    def list_invitations(
        principal: Principal = Depends(require_role("owner", "manager")),
    ) -> list[dict[str, Any]]:
        return [
            {
                "id": invitation.id,
                "email": invitation.email,
                "role": invitation.role,
                "status": invitation.status,
                "expires_at": invitation.expires_at,
            }
            for invitation in auth_service.list_invitations(principal)
        ]

    @app.get("/invitations/{invitation_id}")
    def get_invitation(
        invitation_id: str,
        principal: Principal = Depends(require_role("owner", "manager")),
    ) -> dict[str, Any]:
        # The repository list is already scoped by the reconciled principal's tenant.
        # Treat a foreign identifier exactly like an absent one: a 404 reveals neither
        # the row nor its existence to another tenant.
        for invitation in auth_service.list_invitations(principal):
            if invitation.id == invitation_id:
                return {
                    "id": invitation.id,
                    "email": invitation.email,
                    "role": invitation.role,
                    "status": invitation.status,
                    "expires_at": invitation.expires_at,
                }
        raise HTTPException(status_code=404, detail="Not found")

    @app.delete("/invitations/{invitation_id}")
    def revoke_invitation(
        invitation_id: str,
        principal: Principal = Depends(require_role("owner", "manager")),
    ) -> dict[str, bool]:
        try:
            auth_service.revoke_invitation(principal, invitation_id)
        except InvitationError:
            raise HTTPException(status_code=404, detail="Not found") from None
        return {"ok": True}

    @app.post("/invitations/accept")
    def accept_invitation(
        body: InvitationAcceptRequest,
        response: Response,
        request: Request,
    ) -> dict[str, Any]:
        if "client_ip" in request.scope:
            trusted_client_ip = request.scope["client_ip"]
            if not isinstance(trusted_client_ip, str) or not trusted_client_ip:
                raise HTTPException(status_code=503, detail="temporarily unavailable")
            ip = trusted_client_ip
        else:
            ip = request.client.host if request.client is not None else None
        if ip is None:
            raise HTTPException(status_code=503, detail="temporarily unavailable")
        key = LoginAttemptKey("invite", "accept", ip)
        try:
            ip = key.client_ip_bucket()[1]
        except ValueError:
            raise HTTPException(
                status_code=503,
                detail="temporarily unavailable",
            ) from None
        if ip in {"0.0.0.0", "::"}:
            raise HTTPException(status_code=503, detail="temporarily unavailable")
        attempted_at = datetime.now(UTC)
        try:
            locked = auth_service.reserve_invitation_accept_attempt(ip, now=attempted_at)
        except Exception:
            logging.getLogger(__name__).exception("invitation.accept.throttle_unavailable")
            raise HTTPException(status_code=503, detail="temporarily unavailable") from None
        if locked:
            raise HTTPException(status_code=429, detail="authentication failed")
        try:
            tokens = auth_service.accept_invitation(body.token, body.password, body.display_name)
        except InvitationError:
            raise HTTPException(status_code=401, detail="authentication failed") from None
        try:
            auth_service.mark_invitation_accept_success(ip, now=datetime.now(UTC))
        except Exception:
            # Redemption is already committed by auth_accept_invitation. Preserve its
            # success response if cleanup of the independent throttle row is unavailable;
            # the stale lock expires normally and no other IP's state is affected.
            logging.getLogger(__name__).exception("invitation.accept.throttle_cleanup_failed")
        _set_browser_cookies(response, tokens, BrowserCookieConfig())
        return {
            "access_token": tokens.access_token,
            "refresh_token": tokens.refresh_token,
            "token_type": tokens.token_type,
            "expires_in": tokens.expires_in,
        }

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

    @app.get("/schedule", dependencies=[Depends(require_email_verified)])
    def schedule(
        location_id: Annotated[int, Query(ge=-(2**31), le=2**31 - 1)],
        week_start: date | None = None,
        principal: Principal = Depends(require_role("owner", "manager")),
    ) -> dict[str, Any]:
        return database.fetch_schedule(principal.tenant_id, location_id=location_id, week_start=week_start)

    @app.get("/schedule/shift/{shift_id}/rules", dependencies=[Depends(require_email_verified)])
    def shift_rules(
        shift_id: UUID,
        principal: Principal = Depends(require_role("owner", "manager")),
    ) -> dict[str, list[dict]]:
        return {"rules": database.fetch_shift_rule_evals(principal.tenant_id, shift_id)}

    @app.get("/schedule/shift/{shift_id}/explain", dependencies=[Depends(require_email_verified)])
    def shift_explanation(
        shift_id: UUID,
        principal: Principal = Depends(require_role("owner", "manager")),
    ) -> Explanation:
        try:
            with database._pool_for_request().connection() as conn:
                return explain_assignment(conn, principal.tenant_id, shift_id, model_client=AnthropicExplainer())
        except ShiftNotFound:
            raise HTTPException(status_code=404, detail="Shift not found") from None

    @app.post("/auth/logout")
    def logout(request: Request, response: Response) -> dict[str, bool]:
        access_token = extract_access_credential(request)
        refresh_token = request.cookies.get(BROWSER_REFRESH_COOKIE)
        if access_token is not None:
            with suppress(AuthenticationError):
                auth_service.revoke_token(access_token, refresh_token)
            # Logout is idempotent for expired or otherwise invalid credentials. A
            # persistence failure is deliberately not caught and cannot masquerade as success.
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
            from .config import tenant_routing_config_from_env

            _production_app = create_app(
                build_auth_service_from_env(), routing_config=tenant_routing_config_from_env()
            )
        return _production_app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
