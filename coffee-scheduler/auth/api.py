"""FastAPI-compatible auth routes for CS-061a (F61 AC2).

Four auth endpoints, two transports, no crossover, plus a public JWKS document:

* ``POST /auth/login`` / ``POST /auth/refresh`` — programmatic/bearer. JSON request **and**
  JSON response; the access and refresh tokens travel in the body. No cookie is set or read.
* ``POST /auth/browser/login`` / ``POST /auth/browser/refresh`` — browser sessions. **Both**
  tokens are delivered only as ``HttpOnly; Secure; SameSite`` cookies — the refresh cookie
  scoped to ``Path=/auth/browser``, the short-lived access cookie to ``Path=/`` so later
  requests can present it without JavaScript ever touching a token (XSS defence). The
  response body carries token metadata (``token_type``/``expires_in``) only — never a token.
  The browser refresh route reads the refresh **cookie** only; a request that also carries a
  body is rejected as mixed transport rather than silently accepted.
* ``GET /.well-known/jwks.json`` — the public verification keys (kid-indexed) so a verifier
  can validate access tokens across a key rotation. Public key material only.

Keeping the programmatic and browser paths separate lets CS-070 enforce CSRF and
token-exposure rules on each independently. Route handlers hold no security policy; they
translate transport to/from :class:`~auth.service.TenantAuthService` and map its errors to
HTTP status. No raw token is ever logged.
"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import APIRouter, Cookie, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from .jwt import ACCESS_TOKEN_TTL_SECONDS
from .service import AuthenticationError, IssuedTokens, TenantAuthService
from .tokens import MAX_REFRESH_TOKEN_BYTES, REFRESH_TOKEN_TTL_SECONDS


@dataclass(frozen=True)
class BrowserCookieConfig:
    """Cookie attributes for the browser transport.

    The refresh cookie is path-scoped to ``/auth/browser`` so it is only ever sent to the
    browser refresh route. The access cookie is scoped to ``/`` so every API request can
    present it, and is short-lived (access-token TTL) so a stolen cookie expires fast.
    """

    refresh_name: str = "refresh_token"
    refresh_path: str = "/auth/browser"
    access_name: str = "access_token"
    access_path: str = "/"
    secure: bool = True
    httponly: bool = True
    samesite: str = "strict"
    refresh_max_age: int = REFRESH_TOKEN_TTL_SECONDS
    access_max_age: int = ACCESS_TOKEN_TTL_SECONDS


class LoginRequest(BaseModel):
    # extra="forbid": reject unknown fields rather than ignore them; bounded lengths cap
    # input size so a crafted giant field cannot force wasted work.
    model_config = ConfigDict(extra="forbid")
    tenant_slug: str = Field(min_length=1, max_length=255)
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=1024)


class RefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    refresh_token: str = Field(min_length=1, max_length=MAX_REFRESH_TOKEN_BYTES)


class TokenResponse(BaseModel):
    """Programmatic/bearer response: carries both tokens in the body."""

    access_token: str
    token_type: str
    expires_in: int
    refresh_token: str


class BrowserTokenResponse(BaseModel):
    """Browser response: token *metadata* only. Both tokens are cookie-borne and
    deliberately absent from the body, so no token is ever reachable from JavaScript."""

    token_type: str
    expires_in: int


def _json_body(tokens: IssuedTokens) -> TokenResponse:
    return TokenResponse(
        access_token=tokens.access_token,
        token_type=tokens.token_type,
        expires_in=tokens.expires_in,
        refresh_token=tokens.refresh_token,
    )


def _browser_body(tokens: IssuedTokens) -> BrowserTokenResponse:
    return BrowserTokenResponse(token_type=tokens.token_type, expires_in=tokens.expires_in)


def _set_cookie(response: Response, *, name: str, value: str, path: str, max_age: int, cfg: BrowserCookieConfig) -> None:
    response.set_cookie(
        key=name,
        value=value,
        max_age=max_age,
        path=path,
        secure=cfg.secure,
        httponly=cfg.httponly,
        samesite=cfg.samesite,
    )


def _set_browser_cookies(response: Response, tokens: IssuedTokens, cfg: BrowserCookieConfig) -> None:
    _set_cookie(response, name=cfg.refresh_name, value=tokens.refresh_token, path=cfg.refresh_path, max_age=cfg.refresh_max_age, cfg=cfg)
    # Access cookie tracks the token's real lifetime (expires_in), never longer.
    _set_cookie(response, name=cfg.access_name, value=tokens.access_token, path=cfg.access_path, max_age=tokens.expires_in, cfg=cfg)


def _cookie_pair_count(request: Request, name: str) -> int:
    """Count an exact cookie name in the raw ``Cookie`` headers.

    ``Request.cookies`` (and therefore FastAPI's ``Cookie`` dependency) collapses
    duplicate names.  That is convenient for ordinary cookies but unsafe for the
    browser refresh credential: a proxy and the application can disagree about
    which duplicate value is authoritative.  Treat duplicate refresh-cookie pairs
    as malformed instead of silently selecting one of them.
    """
    count = 0
    for header in request.headers.getlist("cookie"):
        for pair in header.split(";"):
            cookie_name, separator, _ = pair.strip().partition("=")
            if separator and cookie_name == name:
                count += 1
    return count


def create_auth_router(
    service: TenantAuthService, *, cookie_config: BrowserCookieConfig | None = None
) -> APIRouter:
    """Build the auth router bound to ``service``."""
    cfg = cookie_config or BrowserCookieConfig()
    router = APIRouter()

    @router.post("/auth/login", response_model=TokenResponse)
    def login(body: LoginRequest) -> TokenResponse:
        try:
            tokens = service.login_json(
                tenant_slug=body.tenant_slug, email=body.email, password=body.password
            )
        except AuthenticationError:
            raise _unauthorized()
        return _json_body(tokens)

    @router.post("/auth/refresh", response_model=TokenResponse)
    def refresh(body: RefreshRequest) -> TokenResponse:
        try:
            tokens = service.refresh_json(refresh_token=body.refresh_token)
        except AuthenticationError:
            raise _unauthorized()
        return _json_body(tokens)

    @router.post("/auth/browser/login", response_model=BrowserTokenResponse)
    def browser_login(body: LoginRequest, response: Response) -> BrowserTokenResponse:
        try:
            tokens = service.login_browser(
                tenant_slug=body.tenant_slug, email=body.email, password=body.password
            )
        except AuthenticationError:
            raise _unauthorized()
        _set_browser_cookies(response, tokens, cfg)
        return _browser_body(tokens)

    @router.post("/auth/browser/refresh", response_model=BrowserTokenResponse)
    async def browser_refresh(
        request: Request,
        response: Response,
        refresh_token: str | None = Cookie(default=None, alias=cfg.refresh_name),
    ) -> BrowserTokenResponse:
        # Cookie-only: a request that also carries a body is mixed transport (e.g. a valid
        # cookie plus a body refresh_token) and is rejected, not silently honoured.
        if (await request.body()).strip():
            raise _mixed_transport()
        if _cookie_pair_count(request, cfg.refresh_name) != 1 or not refresh_token:
            raise _unauthorized()
        try:
            tokens = service.refresh_browser(refresh_token=refresh_token)
        except AuthenticationError:
            raise _unauthorized()
        _set_browser_cookies(response, tokens, cfg)
        return _browser_body(tokens)

    @router.get("/.well-known/jwks.json")
    def jwks() -> dict:
        # Public verification keys only; the private signing key never leaves the process.
        return service.public_jwks()

    return router


def _unauthorized() -> HTTPException:
    # One opaque 401 for every auth failure (bad credentials, replay, expired). Replay is a
    # subclass of AuthenticationError, so both JSON and browser routes catch it here; the
    # detail never names which check failed and never echoes a token.
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="authentication failed")


def _mixed_transport() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="browser refresh accepts the refresh cookie only; a request body is not permitted",
    )
