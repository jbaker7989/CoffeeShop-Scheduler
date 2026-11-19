"""CS-070 edge-routing contracts (stdlib unittest; no database required)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCHEDULER_ROOT = Path(__file__).resolve().parents[1]
if str(SCHEDULER_ROOT) not in sys.path:
    sys.path.insert(0, str(SCHEDULER_ROOT))


TENANT_A = "00000000-0000-0000-0000-000000000001"
TENANT_B = "00000000-0000-0000-0000-000000000002"
LOGIN = {"tenant_slug": "maven-demo", "email": "owner@maven.example", "password": "correct horse battery staple"}


class _Lookup:
    def __init__(self, values=None) -> None:
        self.values = values or {"maven-demo": TENANT_A, "other": TENANT_B, "trial": "00000000-0000-0000-0000-000000000003"}
        self.calls: list[str] = []

    def __call__(self, slug: str) -> str | None:
        self.calls.append(slug)
        value = self.values.get(slug)
        if isinstance(value, Exception):
            raise value
        return value


class RoutingUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        from api.routing import TenantResolver, TenantRoutingConfig

        self.config = TenantRoutingConfig(mode="production", platform_suffix="coffeescheduler.com")
        self.lookup = _Lookup()
        self.resolver = TenantResolver(self.config, self.lookup)

    def test_production_authority_normalizes_only_safe_aliases(self) -> None:
        route = self.resolver.resolve("MAVEN-DEMO.coffeescheduler.com.:443", "/x")
        self.assertEqual((TENANT_A, "maven-demo", "/x"), (route.tenant_id, route.slug, route.path))
        for authority in (
            "coffeescheduler.com", "a.b.coffeescheduler.com", "127.0.0.1",
            "maven-demo.coffeescheduler.com:444", "user@maven-demo.coffeescheduler.com",
            "maven-demo.coffeescheduler.com,evil.example", "maven-demo.coffeescheduler.com%2f.evil",
            "maven-demo.coffeescheduler.com\\evil", "maven-demo..coffeescheduler.com",
            "-bad.coffeescheduler.com", f"{'a' * 64}.coffeescheduler.com",
        ):
            with self.subTest(authority=authority):
                with self.assertRaises(ValueError):
                    self.resolver.resolve(authority, "/")

    def test_duplicate_or_conflicting_authority_rejected_before_lookup(self) -> None:
        cases = [
            [(b"host", b"maven-demo.coffeescheduler.com"), (b"host", b"maven-demo.coffeescheduler.com")],
            [(b"host", b"maven-demo.coffeescheduler.com"), (b":authority", b"other.coffeescheduler.com")],
            [(b"host", b"maven-demo.coffeescheduler.com"), (b"x-forwarded-host", b"maven-demo.coffeescheduler.com"), (b"x-forwarded-host", b"other.coffeescheduler.com")],
        ]
        for headers in cases:
            with self.subTest(headers=headers):
                with self.assertRaises(ValueError):
                    self.resolver.resolve("maven-demo.coffeescheduler.com", "/", headers)
                self.assertEqual([], self.lookup.calls)

    def test_reserved_slug_and_lookup_failure_are_not_distinguished_or_looked_up(self) -> None:
        from api.routing import ResolutionError

        with self.assertRaises(ResolutionError):
            self.resolver.resolve("admin.coffeescheduler.com", "/")
        self.assertEqual([], self.lookup.calls)
        self.lookup.values["unknown"] = None
        self.lookup.values["broken"] = RuntimeError("db unavailable")
        for slug in ("unknown", "broken"):
            with self.subTest(slug=slug):
                with self.assertRaises(ResolutionError):
                    self.resolver.resolve(f"{slug}.coffeescheduler.com", "/")

    def test_development_path_prefix_is_exact_and_preserves_child_path(self) -> None:
        from api.routing import TenantResolver, TenantRoutingConfig

        config = TenantRoutingConfig(mode="development", platform_suffix="coffeescheduler.com", development_host="localhost")
        resolver = TenantResolver(config, self.lookup)
        prepared = resolver.prepare(
            path="/t/maven-demo/api/x", raw_path=b"/t/maven-demo/api/%2Fkeep",
            headers=[(b"host", b"localhost")], peer_ip="127.0.0.1",
        )
        self.assertEqual(("maven-demo", "/api/%2Fkeep"), (prepared.slug, prepared.path))
        for raw_path in (b"/t/maven-demo", b"/t//x", b"/t/%2fmaven-demo/x"):
            with self.subTest(raw_path=raw_path):
                with self.assertRaises(ValueError):
                    resolver.prepare(path=raw_path.decode("latin-1"), raw_path=raw_path, headers=[(b"host", b"localhost")], peer_ip="127.0.0.1")

    def test_config_fails_closed(self) -> None:
        from api.routing import RoutingConfigurationError, TenantRoutingConfig

        bad = [
            dict(mode="production", platform_suffix="", https_enabled=True),
            dict(mode="production", platform_suffix="*.coffeescheduler.com", https_enabled=True),
            dict(mode="production", platform_suffix="coffeescheduler.com", https_enabled=False),
            dict(mode="development", platform_suffix="coffeescheduler.com", trusted_proxy_cidrs=("not-a-cidr",)),
            dict(mode="development", platform_suffix="api"),
        ]
        for kwargs in bad:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(RoutingConfigurationError):
                    TenantRoutingConfig(**kwargs)


class RoutingMiddlewareTests(unittest.TestCase):
    def setUp(self) -> None:
        from api.app import create_app
        from api.routing import TenantRoutingConfig
        from auth.service import TenantAuthService
        from fastapi.testclient import TestClient

        self.lookup = _Lookup({"maven-demo": TENANT_A, "other": TENANT_B, "trial": "00000000-0000-0000-0000-000000000003", "suspended": None, "churned": None})
        self.fetch_calls: list[str] = []
        self.app = create_app(
            TenantAuthService.for_contract_fixture(),
            fetch_stores=lambda tenant_id: self.fetch_calls.append(tenant_id) or [{"tenant": tenant_id}],
            routing_config=TenantRoutingConfig(mode="production", platform_suffix="coffeescheduler.com"),
            tenant_lookup=self.lookup,
        )
        self.client = TestClient(self.app, base_url="https://maven-demo.coffeescheduler.com")

    def _token(self) -> str:
        response = self.client.post("/auth/login", json=LOGIN)
        self.assertEqual(200, response.status_code, response.text)
        return response.json()["access_token"]

    def test_unknown_suspended_churned_and_lookup_error_have_identical_404(self) -> None:
        samples = []
        for slug in ("unknown", "suspended", "churned"):
            response = self.client.get("/stores", headers={"host": f"{slug}.coffeescheduler.com"})
            samples.append((response.status_code, response.text, response.headers.get("cache-control"), response.headers.get("vary")))
        self.lookup.values["broken"] = RuntimeError("not disclosed")
        response = self.client.get("/stores", headers={"host": "broken.coffeescheduler.com"})
        samples.append((response.status_code, response.text, response.headers.get("cache-control"), response.headers.get("vary")))
        self.assertEqual([(404, "Not Found", "no-store", "Host")] * 4, samples)
        self.assertEqual([], self.fetch_calls)

    def test_trial_is_routable_and_principal_tenant_mismatch_blocks_before_fetch(self) -> None:
        token = self._token()
        okay = self.client.get("/stores", headers={"authorization": f"Bearer {token}"})
        self.assertEqual(200, okay.status_code)
        self.assertEqual([TENANT_A], self.fetch_calls)
        denied = self.client.get("/stores", headers={"host": "other.coffeescheduler.com", "authorization": f"Bearer {token}"})
        self.assertEqual(403, denied.status_code)
        self.assertEqual([TENANT_A], self.fetch_calls)
        # Routing a trial does not claim it can authenticate: the CS-061b state gate is separate.
        trial = self.client.get("/stores", headers={"host": "trial.coffeescheduler.com", "authorization": f"Bearer {token}"})
        self.assertEqual(403, trial.status_code)

    def test_login_body_slug_cannot_override_routed_authority(self) -> None:
        response = self.client.post("/auth/login", json={**LOGIN, "tenant_slug": "other"})
        self.assertEqual(200, response.status_code, response.text)

    def test_development_prefix_rewrites_before_endpoint_dispatch(self) -> None:
        from api.app import create_app
        from api.routing import TenantRoutingConfig
        from auth.service import TenantAuthService
        from fastapi.testclient import TestClient

        seen: list[str] = []
        app = create_app(
            TenantAuthService.for_contract_fixture(), fetch_stores=lambda tenant_id: seen.append(tenant_id) or [],
            routing_config=TenantRoutingConfig(mode="development", platform_suffix="coffeescheduler.com", development_host="localhost", https_enabled=False),
            tenant_lookup=self.lookup,
        )
        client = TestClient(app, base_url="http://localhost")
        login = client.post("/t/maven-demo/auth/login", json=LOGIN)
        self.assertEqual(200, login.status_code, login.text)
        stores = client.get("/t/maven-demo/stores?keep=%2Fraw", headers={"authorization": f"Bearer {login.json()['access_token']}"})
        self.assertEqual(200, stores.status_code, stores.text)
        self.assertEqual([TENANT_A], seen)

    def test_redirect_is_safe_and_pre_lookup_only(self) -> None:
        response = self.client.get(
            "/raw/%2Fkeep?x=%2Fz", headers={"host": "MAVEN-DEMO.coffeescheduler.com.:443"}, follow_redirects=False
        )
        self.assertEqual(308, response.status_code)
        self.assertEqual("https://maven-demo.coffeescheduler.com/raw/%2Fkeep?x=%2Fz", response.headers["location"])
        self.assertEqual("no-store", response.headers["cache-control"])
        self.assertEqual("Host", response.headers["vary"])
        self.assertEqual([], self.lookup.calls)
        unsafe = self.client.post("/stores", headers={"host": "MAVEN-DEMO.coffeescheduler.com.:443"})
        self.assertEqual(404, unsafe.status_code)
        self.assertEqual([], self.lookup.calls)

    def test_origin_csrf_checks_cookie_requests_before_endpoint(self) -> None:
        token = self._token()
        # A bearer plus cookie is intentionally still checked as a cookie-auth request.
        bad = self.client.post(
            "/invitations", json={"email": "x@example.test", "role": "staff"},
            headers={"authorization": f"Bearer {token}", "cookie": f"access_token={token}", "origin": "https://evil.example"},
        )
        self.assertEqual(403, bad.status_code)
        exact = self.client.post(
            "/invitations", json={"email": "x@example.test", "role": "staff"},
            headers={"cookie": f"access_token={token}", "origin": "https://maven-demo.coffeescheduler.com"},
        )
        self.assertEqual(200, exact.status_code, exact.text)
        for origin in ("null", "http://maven-demo.coffeescheduler.com", "https://maven-demo.coffeescheduler.com:444", "https://evil-maven-demo.coffeescheduler.com"):
            response = self.client.post("/stores", headers={"cookie": f"access_token={token}", "origin": origin})
            self.assertEqual(403, response.status_code, origin)

    def test_untrusted_forwarded_host_is_ignored_but_trusted_one_is_used(self) -> None:
        response = self.client.get("/stores", headers={"x-forwarded-host": "other.coffeescheduler.com"})
        self.assertEqual(401, response.status_code)  # direct maven-demo selected, then normal auth failure
        # A dedicated trusted-proxy app accepts exactly one forwarded authority.
        from api.app import create_app
        from api.routing import TenantRoutingConfig
        from auth.service import TenantAuthService
        from fastapi.testclient import TestClient

        app = create_app(TenantAuthService.for_contract_fixture(), fetch_stores=lambda _: [], routing_config=TenantRoutingConfig(mode="production", platform_suffix="coffeescheduler.com", trusted_proxy_cidrs=("203.0.113.0/24",)), tenant_lookup=self.lookup)
        client = TestClient(app, base_url="http://gateway.invalid", client=("203.0.113.8", 44))
        response = client.get("/stores", headers={"x-forwarded-host": "other.coffeescheduler.com"})
        self.assertEqual(401, response.status_code)
        self.assertIn("other", self.lookup.calls)


if __name__ == "__main__":
    unittest.main()
