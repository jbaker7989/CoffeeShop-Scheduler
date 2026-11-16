"""CS-061a / F61 core-auth contracts; intentionally red before implementation.

Run with ``python3 -m unittest coffee-scheduler/test/test_f61_auth_contract.py``.
The tests use no database, PEM files, or local services.  Production supplies the auth
package and its deterministic in-memory contract fixture; this test suite never supplies
a permissive replacement implementation.
"""
from __future__ import annotations

import base64
import inspect
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCHEDULER_ROOT = ROOT / "coffee-scheduler"
MIGRATION = SCHEDULER_ROOT / "db" / "migrations" / "versions" / "0010_cs061a_auth.py"
if str(SCHEDULER_ROOT) not in sys.path:
    sys.path.insert(0, str(SCHEDULER_ROOT))


def b64url_json(value: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":")).encode()).rstrip(b"=").decode()


def tamper_payload(token: str) -> str:
    header, payload, signature = token.split(".")
    decoded = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    decoded["role"] = "owner" if decoded["role"] != "owner" else "manager"
    return ".".join((header, b64url_json(decoded), signature))


class F61IdentityAndPersistenceContractTests(unittest.TestCase):
    # spec: F61.ac1 — tenant identities use a four-value role allowlist, tenant-local
    # case-insensitive email uniqueness, Argon2id, separate global admins, and opaque
    # refresh-family persistence.
    def test_migration_defines_identity_refresh_and_least_privilege_contract(self) -> None:
        self.assertTrue(MIGRATION.is_file(), "CS-061a must add migration 0010_cs061a_auth.py")
        source = MIGRATION.read_text(encoding="utf-8")
        for fragment in (
            'revision = "0010"',
            'down_revision = "0009"',
            "CREATE EXTENSION IF NOT EXISTS citext",
            "CREATE TABLE tenant_users",
            "tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE",
            "UNIQUE (tenant_id, email)",
            "'owner', 'manager', 'staff', 'read_only'",
            "password_hash TEXT NOT NULL",
            "is_active BOOLEAN NOT NULL DEFAULT TRUE",
            "CREATE TABLE saas_admins",
            "mfa_secret_ciphertext",
            "CREATE TABLE auth_refresh_tokens",
            "family_id UUID NOT NULL",
            "token_digest TEXT NOT NULL UNIQUE",
            "rotated_at TIMESTAMPTZ NULL",
            "revoked_at TIMESTAMPTZ NULL",
            "FOREIGN KEY (tenant_id, user_id) REFERENCES tenant_users (tenant_id, id)",
            "CREATE ROLE auth_svc NOLOGIN",
            "SECURITY DEFINER SET search_path = pg_catalog, public",
            "REVOKE ALL ON FUNCTION",
            "GRANT EXECUTE ON FUNCTION",
        ):
            self.assertIn(fragment, source)
        self.assertNotIn("mfa_secret TEXT", source, "MFA material must not be plaintext")

    # spec: F61.ac1 — password services create and accept only Argon2id encodings; inactive
    # identities and damaged stored hashes fail like invalid credentials.
    def test_argon2id_and_safe_login_failure_contract(self) -> None:
        from auth.passwords import Argon2idPasswordService, InvalidPasswordHash
        from auth.service import AuthenticationError, TenantAuthService

        passwords = Argon2idPasswordService()
        encoded = passwords.hash("correct horse battery staple")
        self.assertTrue(encoded.startswith("$argon2id$"))
        self.assertNotEqual(encoded, "correct horse battery staple")
        self.assertTrue(passwords.verify("correct horse battery staple", encoded))
        self.assertFalse(passwords.verify("incorrect", encoded))
        with self.assertRaises(InvalidPasswordHash):
            passwords.verify("correct horse battery staple", "$argon2i$wrong-format")

        for kwargs in (
            {"user_is_active": False},
            {"user_exists": False},
            {"stored_password_hash": "$argon2i$wrong-format"},
        ):
            service = TenantAuthService.for_contract_fixture(**kwargs)
            with self.assertRaisesRegex(AuthenticationError, "authentication failed"):
                service.login_json(
                    tenant_slug="maven-demo", email="owner@maven.example", password="incorrect"
                )


class F61JwtAndTenantAuthorityContractTests(unittest.TestCase):
    # spec: F61.ac2 — access JWTs are 15-minute RS256 credentials selected by known kid;
    # neither alg=none nor algorithm/key confusion is accepted.
    def test_rs256_keyring_configuration_and_known_kid_only(self) -> None:
        from auth.jwt import (
            ACCESS_TOKEN_TTL_SECONDS,
            ALLOWED_JWT_ALGORITHMS,
            RS256KeyRing,
            RS256TokenService,
            TokenValidationError,
        )
        from auth.service import AuthenticationError, TenantAuthService

        self.assertEqual(900, ACCESS_TOKEN_TTL_SECONDS)
        self.assertEqual(("RS256",), ALLOWED_JWT_ALGORITHMS)
        params = inspect.signature(RS256KeyRing.from_environment).parameters
        self.assertIn("AUTH_JWT_PRIVATE_KEY_PEM", params)
        self.assertIn("AUTH_JWT_PUBLIC_KEYS_PEM", params)
        self.assertIn("AUTH_JWT_ACTIVE_KID", params)
        self.assertTrue(callable(RS256TokenService.issue_access_token))
        self.assertTrue(callable(RS256TokenService.validate_access_token))
        self.assertTrue(issubclass(TokenValidationError, Exception))

        service = TenantAuthService.for_contract_fixture()
        issued = service.login_json(
            tenant_slug="maven-demo", email="owner@maven.example", password="correct horse battery staple"
        )
        header, payload, _ = issued.access_token.split(".")
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        self.assertEqual("RS256", json.loads(base64.urlsafe_b64decode(header + "=" * (-len(header) % 4)))["alg"])
        self.assertIn("kid", json.loads(base64.urlsafe_b64decode(header + "=" * (-len(header) % 4))))
        self.assertEqual(claims["sub"], claims["user_id"])
        self.assertFalse(claims["is_saas_admin"])
        for forbidden in (
            ".".join((b64url_json({"alg": "none", "kid": "unknown"}), payload, "")),
            ".".join((b64url_json({"alg": "HS256", "kid": "unknown"}), payload, "signature")),
            ".".join((b64url_json({"alg": "ES256", "kid": "unknown"}), payload, "signature")),
        ):
            with self.assertRaises(AuthenticationError):
                service.authenticate_access_token(forbidden)

    # spec: F61.ac2 — issuer/audience, expiry, nbf, signature, claim shape and canonical
    # tenant membership all fail closed before an F60 session context can be opened.
    def test_tamper_temporal_claim_and_cross_tenant_escape_rejection(self) -> None:
        from auth.service import AuthenticationError, TenantAuthService

        service = TenantAuthService.for_contract_fixture()
        token = service.login_json(
            tenant_slug="maven-demo", email="owner@maven.example", password="correct horse battery staple"
        ).access_token
        with self.assertRaises(AuthenticationError):
            service.authenticate_access_token(tamper_payload(token))

        for claim_overrides in (
            {"iss": "wrong-issuer"}, {"aud": "wrong-audience"},
            {"exp": 1}, {"nbf": 4_102_444_800}, {"jti": ""},
            {"tenant_id": "not-a-uuid"}, {"role": "suspended"},
            {"is_saas_admin": True}, {"sub": "00000000-0000-0000-0000-000000000099"},
        ):
            forged = service.issue_contract_token(claim_overrides=claim_overrides)
            with self.assertRaises(AuthenticationError, msg=str(claim_overrides)):
                service.authenticate_access_token(forged)

        forged_membership = service.issue_contract_token(
            tenant_id="00000000-0000-0000-0000-000000000002",
            user_id="00000000-0000-0000-0000-000000000001",
            role="owner",
        )
        with self.assertRaises(AuthenticationError):
            service.authenticate_access_token(forged_membership)


class F61RefreshAndTransportContractTests(unittest.TestCase):
    # spec: F61.ac2 — refresh secrets are opaque seven-day credentials, rotate exactly once,
    # and any replay burns the whole family rather than retaining the newest child.
    def test_refresh_rotation_reuse_and_family_binding(self) -> None:
        from auth.service import AuthenticationError, RefreshReplayError, TenantAuthService
        from auth.tokens import REFRESH_TOKEN_TTL_SECONDS

        self.assertEqual(7 * 24 * 60 * 60, REFRESH_TOKEN_TTL_SECONDS)
        service = TenantAuthService.for_contract_fixture()
        initial = service.login_json(
            tenant_slug="maven-demo", email="owner@maven.example", password="correct horse battery staple"
        )
        self.assertNotIn(".", initial.refresh_token, "refresh credentials are opaque, never JWTs")
        rotated = service.refresh_json(refresh_token=initial.refresh_token)
        self.assertNotEqual(initial.refresh_token, rotated.refresh_token)
        with self.assertRaises(RefreshReplayError):
            service.refresh_json(refresh_token=initial.refresh_token)
        with self.assertRaises(RefreshReplayError):
            service.refresh_json(refresh_token=rotated.refresh_token)
        for invalid in ("", "not-a-known-refresh", "x" * 8193):
            with self.assertRaises(AuthenticationError):
                service.refresh_json(refresh_token=invalid)

    # spec: F61.ac2 — JSON and browser refresh paths never cross-accept tokens, and browser
    # refresh transport is confidential rather than response-body leakage.
    def test_router_exposes_only_separate_json_and_browser_transports(self) -> None:
        from auth.api import BrowserCookieConfig, BrowserTokenResponse, TokenResponse, create_auth_router
        from auth.service import TenantAuthService

        router = create_auth_router(TenantAuthService.for_contract_fixture())
        routes = {route.path: route for route in router.routes}
        self.assertEqual(
            {
                "/auth/login",
                "/auth/refresh",
                "/auth/browser/login",
                "/auth/browser/refresh",
                "/.well-known/jwks.json",
            },
            set(routes),
        )
        for path in ("/auth/login", "/auth/refresh"):
            self.assertIs(routes[path].response_model, TokenResponse)
        for path in ("/auth/browser/login", "/auth/browser/refresh"):
            self.assertIs(routes[path].response_model, BrowserTokenResponse)
        self.assertNotIn("access_token", BrowserTokenResponse.model_fields)
        self.assertNotIn("refresh_token", BrowserTokenResponse.model_fields)
        cfg = BrowserCookieConfig()
        self.assertEqual("/auth/browser", cfg.refresh_path)
        self.assertTrue(cfg.httponly)
        self.assertTrue(cfg.secure)
        self.assertEqual("strict", cfg.samesite)


if __name__ == "__main__":
    unittest.main()
