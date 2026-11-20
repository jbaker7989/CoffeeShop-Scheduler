"""CS-068b / F68 HTTP-auth multi-tenant isolation gate.

This is intentionally a rollback-only, self-seeding integration gate.  It creates
two tenant owners through ``POST /signup`` on one direct PostgreSQL connection; a
commit-suppressing facade keeps the production repository's per-operation commits
inside the enclosing test transaction, which ``tearDown`` always rolls back.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEDULER_ROOT = Path(__file__).resolve().parents[1]
if str(SCHEDULER_ROOT) not in sys.path:
    sys.path.insert(0, str(SCHEDULER_ROOT))

GATEWAY_DSN = os.environ.get(
    "GATEWAY_DATABASE_URL", "postgresql://coffeeshop:coffeeshop@localhost:6432/coffeeshop"
)
DIRECT_DSN = os.environ.get(
    "DATABASE_URL", "postgresql://coffeeshop:coffeeshop@localhost:5432/coffeeshop"
).replace("postgresql+psycopg://", "postgresql://")

_SKIP = None
try:
    import psycopg
    from fastapi.testclient import TestClient
    from api.app import create_app
    from api.routing import TenantRoutingConfig
    from auth.passwords import Argon2idPasswordService
    from auth.repository import DatabaseAuthRepository
    from auth.service import TenantAuthService, _build_contract_token_service
    from auth.tokens import OpaqueRefreshTokenService
    with psycopg.connect(GATEWAY_DSN, connect_timeout=3, prepare_threshold=None) as _probe:
        _probe.execute("SELECT 1")
    with psycopg.connect(DIRECT_DSN, connect_timeout=3, prepare_threshold=None) as _probe:
        _probe.execute("SELECT 1")
except Exception as exc:  # pragma: no cover - local service/dependency dependent
    _SKIP = f"gateway deps or database unavailable: {exc}"


class _RollbackConnection:
    """Keep repository ``commit()`` calls from escaping this test's transaction."""

    def __init__(self, connection) -> None:
        self._connection = connection

    def commit(self) -> None:
        # DatabaseAuthRepository is intentionally autocommit-friendly in production.
        # This gate instead owns one transaction and tears it down with rollback.
        return None

    def __getattr__(self, name):
        return getattr(self._connection, name)


def _b64url_json(value: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":")).encode()).rstrip(b"=").decode()


@unittest.skipIf(_SKIP, _SKIP or "")
class HttpIsolationHarness(unittest.TestCase):
    """One fresh A/B signup pair per test; no shared seed identity is ever trusted."""

    def setUp(self) -> None:
        self.connection = psycopg.connect(DIRECT_DSN, prepare_threshold=None)
        self.repository_connection = _RollbackConnection(self.connection)
        self.token_service = _build_contract_token_service()
        self.service = TenantAuthService(
            repository=DatabaseAuthRepository(self.repository_connection),
            password_service=Argon2idPasswordService(),
            token_service=self.token_service,
            refresh_token_service=OpaqueRefreshTokenService(pepper="g" * 48),
        )
        self.slug_a = f"cs068a-{uuid.uuid4().hex[:18]}"
        self.slug_b = f"cs068b-{uuid.uuid4().hex[:18]}"
        self.email_a = f"owner-a-{uuid.uuid4().hex[:10]}@example.test"
        self.email_b = f"owner-b-{uuid.uuid4().hex[:10]}@example.test"
        self.app = create_app(
            self.service,
            fetch_stores=self._fetch_stores,
            tenant_lookup=self._active_tenant_id,
        )
        self.client = TestClient(self.app, client=("127.0.0.1", 50000))
        self.a = self._signup(self.slug_a, self.email_a)
        self.b = self._signup(self.slug_b, self.email_b)
        # Signup starts a trial; activate only the two ephemeral test rows so this
        # gate can prove the active-access boundary.  The signup itself created the
        # tenant/users and rollback removes both the setup and all test mutations.
        self.connection.execute("UPDATE tenants SET status='active' WHERE id IN (%s, %s)", (self.a["tenant_id"], self.b["tenant_id"]))

    def tearDown(self) -> None:
        try:
            self.connection.rollback()
        finally:
            self.connection.close()

    def _signup(self, slug: str, email: str) -> dict:
        response = self.client.post(
            "/signup",
            json={
                "slug": slug, "name": f"CS-068b {slug}", "jurisdiction": "NY-STATE+NYC",
                "email": email, "password": "correct horse battery staple",
            },
        )
        self.assertEqual(201, response.status_code, response.text)
        return response.json()

    def _headers(self, issued: dict) -> dict[str, str]:
        return {"authorization": f"Bearer {issued['access_token']}"}

    def _active_tenant_id(self, slug: str) -> str | None:
        row = self.connection.execute(
            "SELECT id FROM tenants WHERE slug=%s AND status IN ('active', 'trial')", (slug,)
        ).fetchone()
        return None if row is None else str(row[0])

    def _fetch_stores(self, tenant_id: str) -> list[dict]:
        # The DB-layer RLS proof lives in CS-068a.  Here this records the sole tenant
        # identity the HTTP boundary hands to the data plane, without borrowing a
        # separate connection that cannot observe this test's rollback-only signups.
        row = self.connection.execute("SELECT id, slug FROM tenants WHERE id=%s", (tenant_id,)).fetchone()
        return [] if row is None else [{"tenant_id": str(row[0]), "slug": row[1]}]

    def _signed_token(self, *, tenant_id: str | None = None, user_id: str | None = None,
                      role: str = "owner", overrides: dict | None = None) -> str:
        now = int(datetime.now(timezone.utc).timestamp())
        claims = {
            "iss": self.token_service.issuer, "aud": self.token_service.audience,
            "sub": user_id or self.a["user_id"], "tenant_id": tenant_id or self.a["tenant_id"],
            "user_id": user_id or self.a["user_id"], "role": role, "is_saas_admin": False,
            "jti": str(uuid.uuid4()), "iat": now, "nbf": now, "exp": now + self.token_service.ttl_seconds,
        }
        if overrides:
            claims.update(overrides)
        # Test-only RS256 signer: this proves each rejection is claims/reconciliation
        # based rather than an accidental signature mismatch.
        return self.token_service.sign_unchecked(claims)

    def _routed_client(self) -> TestClient:
        app = create_app(
            self.service, fetch_stores=self._fetch_stores,
            routing_config=TenantRoutingConfig(
                mode="development", platform_suffix="coffeescheduler.com",
                development_host="localhost", https_enabled=False,
            ),
            tenant_lookup=self._active_tenant_id,
        )
        return TestClient(app, base_url="http://localhost", client=("127.0.0.1", 50001))

    def _assert_generic(self, response, *secrets: str) -> None:
        body = response.text.lower()
        self.assertNotIn("traceback", body)
        self.assertNotIn("internal", body)
        for secret in secrets:
            self.assertNotIn(secret.lower(), body)

    # spec: F68.ac2 — missing, malformed, expired, tampered, alg=none, wrong iss/aud, and non-member access credentials fail closed.
    def test_fail_closed_access_credentials(self) -> None:
        cases = [
            ({}, 401),
            ({"cookie": "access_token="}, 401),
            ({"cookie": "access_token=not-a-jwt"}, 401),
            ({"authorization": "Bearer malformed"}, 401),
            ({"authorization": f"Bearer {self._signed_token(overrides={'exp': 1})}"}, 401),
            ({"authorization": f"Bearer {self._signed_token(overrides={'iss': 'wrong-issuer'})}"}, 401),
            ({"authorization": f"Bearer {self._signed_token(overrides={'aud': 'wrong-audience'})}"}, 401),
            ({"authorization": f"Bearer {self._signed_token(tenant_id=self.b['tenant_id'], user_id=self.a['user_id'])}"}, 401),
        ]
        valid = self._signed_token()
        header, payload, signature = valid.split(".")
        none = ".".join((_b64url_json({"alg": "none", "kid": "cs061a-contract"}), payload, signature))
        cases.append(({"authorization": f"Bearer {none}"}, 401))
        # Alter the first base64url quantum: changing a final character can leave
        # unused trailing bits intact and accidentally decode to the same signature.
        tampered = ".".join((header, payload, ("A" if signature[0] != "A" else "B") + signature[1:]))
        cases.append(({"authorization": f"Bearer {tampered}"}, 401))
        for headers, expected in cases:
            with self.subTest(headers=headers):
                response = self.client.get("/me", headers=headers)
                self.assertEqual(expected, response.status_code, response.text)
                self._assert_generic(response, self.email_a, self.a["tenant_id"], self.slug_a)

    # spec: F68.ac4 — a correctly signed tenant token carrying an admin flag is never an escalation credential.
    def test_tenant_token_cannot_escalate_to_saas_admin(self) -> None:
        response = self.client.get("/me", headers={"authorization": f"Bearer {self._signed_token(overrides={'is_saas_admin': True})}"})
        self.assertEqual(401, response.status_code, response.text)
        self._assert_generic(response, self.email_a, self.a["tenant_id"], self.slug_a)

    # spec: F68.ac2 — /me and /stores use only the reconciled tenant identity.
    def test_cross_tenant_reads_are_bound_to_the_principal(self) -> None:
        for issued in (self.a, self.b):
            with self.subTest(tenant=issued["tenant_id"]):
                me = self.client.get("/me", headers=self._headers(issued))
                stores = self.client.get("/stores", headers=self._headers(issued))
                self.assertEqual(200, me.status_code, me.text)
                self.assertEqual(issued["tenant_id"], me.json()["tenant_id"])
                self.assertEqual(200, stores.status_code, stores.text)
                self.assertEqual([issued["tenant_id"]], [item["tenant_id"] for item in stores.json()["stores"]])

    # spec: F68.ac2 — foreign invitation identifiers are indistinguishable from absent rows and cannot be revoked.
    def test_foreign_invitation_is_404_and_cannot_be_mutated(self) -> None:
        created = self.client.post("/invitations", json={"email": "invitee@example.test", "role": "staff"}, headers=self._headers(self.b))
        self.assertEqual(200, created.status_code, created.text)
        invitation_id = created.json()["invitation_id"]
        foreign_get = self.client.get(f"/invitations/{invitation_id}", headers=self._headers(self.a))
        absent_get = self.client.get(f"/invitations/{uuid.uuid4()}", headers=self._headers(self.a))
        foreign_delete = self.client.delete(f"/invitations/{invitation_id}", headers=self._headers(self.a))
        absent_delete = self.client.delete(f"/invitations/{uuid.uuid4()}", headers=self._headers(self.a))
        self.assertEqual(404, foreign_get.status_code)
        self.assertEqual((foreign_get.status_code, foreign_get.content), (absent_get.status_code, absent_get.content))
        self.assertEqual((foreign_delete.status_code, foreign_delete.content), (absent_delete.status_code, absent_delete.content))
        still_b = self.client.get(f"/invitations/{invitation_id}", headers=self._headers(self.b))
        self.assertEqual(200, still_b.status_code, still_b.text)

    # spec: F68.ac2 — a principal on another resolved tenant route is forbidden before data access; unroutable names are uniformly absent.
    def test_routing_binds_principal_and_hides_unroutable_tenants(self) -> None:
        client = self._routed_client()
        b_route = client.get(f"/t/{self.slug_b}/me", headers=self._headers(self.a))
        self.assertEqual(403, b_route.status_code, b_route.text)
        self.connection.execute("UPDATE tenants SET status='suspended' WHERE id=%s", (self.b["tenant_id"],))
        responses = [
            client.get("/t/no-such-tenant/me"),
            client.get(f"/t/{self.slug_b}/me"),
            client.get("/t/admin/me"),
        ]
        self.assertEqual([(404, b"Not Found")] * 3, [(response.status_code, response.content) for response in responses])
        for response in responses:
            self.assertEqual("no-store", response.headers.get("cache-control"))
            self.assertEqual("Host", response.headers.get("vary"))

    # spec: F68.ac2 — active access is rechecked per request, so suspension takes effect before the access JWT expires.
    def test_suspension_revokes_protected_access_within_token_ttl(self) -> None:
        self.assertEqual(200, self.client.get("/me", headers=self._headers(self.a)).status_code)
        self.connection.execute("UPDATE tenants SET status='suspended' WHERE id=%s", (self.a["tenant_id"],))
        for method, path, kwargs in (
            ("get", "/me", {}), ("get", "/stores", {}), ("get", "/invitations", {}),
            ("post", "/invitations", {"json": {"email": "blocked@example.test", "role": "staff"}}),
        ):
            with self.subTest(path=path, method=method):
                response = getattr(self.client, method)(path, headers=self._headers(self.a), **kwargs)
                self.assertEqual(403, response.status_code, response.text)
                self._assert_generic(response, self.email_a, self.a["tenant_id"], self.slug_a)

    # spec: F68.ac2 — unknown and suspended tenant routes, and foreign and absent resources, disclose no different body.
    def test_no_existence_disclosure_has_identical_responses(self) -> None:
        client = self._routed_client()
        self.connection.execute("UPDATE tenants SET status='suspended' WHERE id=%s", (self.b["tenant_id"],))
        unknown = client.get("/t/no-such-tenant/stores")
        suspended = client.get(f"/t/{self.slug_b}/stores")
        self.assertEqual((unknown.status_code, unknown.content), (suspended.status_code, suspended.content))
        created = self.client.post("/invitations", json={"email": "private@example.test", "role": "staff"}, headers=self._headers(self.b))
        # B is suspended, so create the resource through A and compare it from B's valid-but-foreign session.
        if created.status_code != 200:
            self.connection.execute("UPDATE tenants SET status='active' WHERE id=%s", (self.b["tenant_id"],))
            created = self.client.post("/invitations", json={"email": "private@example.test", "role": "staff"}, headers=self._headers(self.b))
        self.assertEqual(200, created.status_code, created.text)
        self.connection.execute("UPDATE tenants SET status='suspended' WHERE id=%s", (self.b["tenant_id"],))
        foreign = self.client.get(f"/invitations/{created.json()['invitation_id']}", headers=self._headers(self.a))
        absent = self.client.get(f"/invitations/{uuid.uuid4()}", headers=self._headers(self.a))
        self.assertEqual((404, foreign.content), (absent.status_code, absent.content))

    # spec: F68.ac4 — refresh rejects tampered, expired, foreign tokens and burns an entire replayed family without crossing tenants.
    def test_refresh_rotation_replay_and_tenant_binding(self) -> None:
        expired = self.a["refresh_token"]
        self.connection.execute(
            "UPDATE auth_refresh_tokens SET expires_at=%s WHERE token_digest=%s",
            (datetime.now(timezone.utc) - timedelta(seconds=1), self.service._refresh.digest(expired)),
        )
        foreign_refresh = TenantAuthService.for_contract_fixture().login_json(
            tenant_slug="maven-demo", email="owner@maven.example", password="correct horse battery staple"
        ).refresh_token
        for token in ("not-a-refresh-token", expired, foreign_refresh):
            response = self.client.post("/auth/refresh", json={"refresh_token": token})
            self.assertEqual(401, response.status_code, response.text)
        # Seed a fresh A/B pair after the deliberate expiry update and show rotation is
        # tenant-derived (the request contains no tenant selector).
        a = self._signup(f"cs068r-{uuid.uuid4().hex[:18]}", f"refresh-a-{uuid.uuid4().hex[:10]}@example.test")
        b = self._signup(f"cs068s-{uuid.uuid4().hex[:18]}", f"refresh-b-{uuid.uuid4().hex[:10]}@example.test")
        self.connection.execute("UPDATE tenants SET status='active' WHERE id IN (%s, %s)", (a["tenant_id"], b["tenant_id"]))
        rotated = self.client.post("/auth/refresh", json={"refresh_token": a["refresh_token"]})
        self.assertEqual(200, rotated.status_code, rotated.text)
        a_me = self.client.get("/me", headers={"authorization": f"Bearer {rotated.json()['access_token']}"})
        self.assertEqual(a["tenant_id"], a_me.json()["tenant_id"])
        self.assertEqual(401, self.client.post("/auth/refresh", json={"refresh_token": a["refresh_token"]}).status_code)
        self.assertEqual(401, self.client.post("/auth/refresh", json={"refresh_token": rotated.json()["refresh_token"]}).status_code)
        b_me = self.client.get("/me", headers={"authorization": f"Bearer {b['access_token']}"})
        self.assertEqual(b["tenant_id"], b_me.json()["tenant_id"])

    # spec: F68.ac4 — success, errors, and routing redirects are never shared through a cache across tenant boundaries.
    def test_tenant_responses_and_errors_are_non_cacheable(self) -> None:
        same_origin = [
            self.client.get("/me", headers=self._headers(self.a)),
            self.client.get("/me"),
            self.client.post("/auth/login", json={"tenant_slug": self.slug_a, "email": self.email_a, "password": "wrong"}),
        ]
        client = self._routed_client()
        routed = client.get(f"/t/{self.slug_a}/me", headers=self._headers(self.a))
        unknown = client.get("/t/no-such-tenant/me")
        production = TestClient(
            create_app(
                self.service, fetch_stores=self._fetch_stores,
                routing_config=TenantRoutingConfig(mode="production", platform_suffix="coffeescheduler.com"),
                tenant_lookup=self._active_tenant_id,
            ),
            base_url=f"https://{self.slug_a}.coffeescheduler.com", client=("127.0.0.1", 50002),
        )
        redirect = production.get(
            "/me", headers={"host": f"{self.slug_a.upper()}.coffeescheduler.com.:443"}, follow_redirects=False,
        )
        self.assertEqual(308, redirect.status_code, redirect.text)
        for response in same_origin + [routed, unknown, redirect]:
            self.assertEqual("no-store", response.headers.get("cache-control"), response.headers)
        for response in (routed, unknown, redirect):
            self.assertIn("host", response.headers.get("vary", "").lower())

    # spec: F68.ac4 — authorization and lookup failures are generic and login does not enumerate tenant/email existence.
    def test_error_bodies_scrub_pii_and_login_enumeration(self) -> None:
        bad_password = self.client.post("/auth/login", json={"tenant_slug": self.slug_a, "email": self.email_a, "password": "wrong"})
        bad_tenant = self.client.post("/auth/login", json={"tenant_slug": "no-such-tenant", "email": self.email_a, "password": "wrong"})
        self.assertEqual((401, bad_password.content), (bad_tenant.status_code, bad_tenant.content))
        client = self._routed_client()
        route_forbidden = client.get(f"/t/{self.slug_b}/me", headers=self._headers(self.a))
        not_found = client.get("/t/no-such-tenant/me")
        for response in (bad_password, route_forbidden, not_found):
            self._assert_generic(response, self.email_a, self.email_b, self.a["tenant_id"], self.b["tenant_id"], self.slug_a, self.slug_b)


class CoverageGate(unittest.TestCase):
    # spec: F68.ac5 — every CS-068b test stays visibly tied to an F68 acceptance criterion.
    def test_every_test_method_has_an_f68_spec_tag(self) -> None:
        source = Path(__file__).read_text(encoding="utf-8")
        import ast
        tree = ast.parse(source)
        missing = []
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                preceding = source.splitlines()[max(0, node.lineno - 3):node.lineno - 1]
                if not any("spec: F68.ac" in line for line in preceding):
                    missing.append(node.name)
        self.assertEqual([], missing, f"missing F68 spec tag(s): {missing}")


if __name__ == "__main__":
    unittest.main()
