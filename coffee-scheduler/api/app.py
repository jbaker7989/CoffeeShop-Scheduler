"""Minimal same-origin API gateway for the manager UI."""
from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from pydantic import BaseModel

from .auth import clear_session_cookie, issue_token, set_session_cookie, verify_password, verify_token
from .config import DEMO_MANAGER_EMAIL, DEMO_MANAGER_PASSWORD_HASH, DEMO_TENANT_SLUG
from .db import fetch_stores, resolve_tenant_id


app = FastAPI()

_INVALID_CREDENTIALS = "Invalid credentials"


class LoginRequest(BaseModel):
    tenant_slug: str
    email: str
    password: str


def current_claims(request: Request) -> dict[str, Any]:
    token = request.cookies.get("session")
    claims = verify_token(token) if token else None
    if claims is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return claims


@app.post("/auth/login")
def login(credentials: LoginRequest, response: Response) -> dict[str, str]:
    tenant_id = resolve_tenant_id(credentials.tenant_slug)
    if (
        tenant_id is None
        or credentials.tenant_slug != DEMO_TENANT_SLUG
        or credentials.email != DEMO_MANAGER_EMAIL
        or not verify_password(DEMO_MANAGER_PASSWORD_HASH, credentials.password)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_INVALID_CREDENTIALS,
        )

    token = issue_token(
        {
            "tenant_id": tenant_id,
            "tenant_slug": credentials.tenant_slug,
            "user_id": "demo-manager",
            "role": "manager",
        }
    )
    set_session_cookie(response, token)
    return {"role": "manager", "tenant_slug": credentials.tenant_slug}


@app.post("/auth/logout")
def logout(response: Response) -> dict[str, bool]:
    clear_session_cookie(response)
    return {"ok": True}


@app.get("/me")
def me(claims: dict[str, Any] = Depends(current_claims)) -> dict[str, Any]:
    return {"tenant_slug": claims["tenant_slug"], "role": claims["role"]}


@app.get("/stores")
def stores(claims: dict[str, Any] = Depends(current_claims)) -> dict[str, list[dict]]:
    # The tenant id is read only from the signed, verified session claims.
    return {"stores": fetch_stores(claims["tenant_id"])}


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}
