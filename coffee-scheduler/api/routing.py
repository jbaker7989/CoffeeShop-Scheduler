"""Tenant selection at the HTTP edge for CS-070.

This module intentionally has no database/session state.  It validates the raw ASGI
authority first, resolves one routable slug, and leaves the sole RLS binding to
``data_access.session.open_session_context`` in the data access layer.
"""
from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import os
import re
from typing import Callable, Iterable, Mapping
from urllib.parse import urlsplit

from starlette.responses import PlainTextResponse, RedirectResponse


RESERVED_SLUGS = frozenset({
    "admin", "www", "api", "app", "auth", "billing", "status", "static", "assets",
})

_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_SLUG = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class RoutingConfigurationError(ValueError):
    """Routing cannot safely start with the supplied configuration."""


class ResolutionError(ValueError):
    """An untrusted authority/path cannot select a tenant."""


@dataclass(frozen=True)
class TenantRoute:
    tenant_id: str
    slug: str
    path: str


@dataclass(frozen=True)
class _Authority:
    host: str
    port: int | None
    was_alias: bool


@dataclass(frozen=True)
class _PreparedRoute:
    slug: str
    path: str
    was_alias: bool


@dataclass(frozen=True)
class TenantRoutingConfig:
    """Validated, explicit public-topology configuration.

    ``development_port`` is deliberately opt-in: no arbitrary localhost port is
    accepted merely because the application is running in development.
    """

    mode: str
    platform_suffix: str
    development_host: str = "localhost"
    development_prefix: str = "/t/"
    trusted_proxy_cidrs: tuple[str, ...] = ()
    https_enabled: bool = True
    development_port: int | None = None

    def __post_init__(self) -> None:
        mode = self.mode.lower()
        if mode == "prod":
            mode = "production"
        if mode not in {"production", "development"}:
            raise RoutingConfigurationError("routing mode must be production or development")
        object.__setattr__(self, "mode", mode)
        suffix = _validate_dns_name(self.platform_suffix, what="platform suffix")
        object.__setattr__(self, "platform_suffix", suffix)
        host = _validate_dns_name(self.development_host, what="development host")
        object.__setattr__(self, "development_host", host)
        if not self.development_prefix.startswith("/") or not self.development_prefix.endswith("/"):
            raise RoutingConfigurationError("development prefix must begin and end with '/'")
        if "//" in self.development_prefix or "%" in self.development_prefix or "\\" in self.development_prefix:
            raise RoutingConfigurationError("development prefix is malformed")
        if mode == "production" and not self.https_enabled:
            raise RoutingConfigurationError("HTTPS cannot be disabled in production")
        if self.development_port is not None and not (1 <= self.development_port <= 65535):
            raise RoutingConfigurationError("development port is invalid")
        try:
            tuple(ipaddress.ip_network(value, strict=False) for value in self.trusted_proxy_cidrs)
        except ValueError as exc:
            raise RoutingConfigurationError("trusted proxy CIDR is invalid") from exc
        # A bare reserved routing host would make the administrative namespace look
        # like a tenant suffix.  Tenant labels themselves are checked before lookup.
        if suffix in RESERVED_SLUGS or host in RESERVED_SLUGS:
            raise RoutingConfigurationError("reserved namespace collides with routing host")

    @property
    def trusted_networks(self) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
        return tuple(ipaddress.ip_network(value, strict=False) for value in self.trusted_proxy_cidrs)

    @classmethod
    def from_environment(cls, env: Mapping[str, str] | None = None) -> "TenantRoutingConfig":
        source = os.environ if env is None else env
        mode = source.get("TENANT_ROUTING_MODE", "development")
        suffix = source.get("TENANT_PLATFORM_SUFFIX", "coffeescheduler.com")
        cidrs = tuple(value.strip() for value in source.get("TRUSTED_PROXY_CIDRS", "").split(",") if value.strip())
        port_value = source.get("TENANT_DEVELOPMENT_PORT", "")
        try:
            port = int(port_value) if port_value else None
        except ValueError as exc:
            raise RoutingConfigurationError("development port is invalid") from exc
        return cls(
            mode=mode,
            platform_suffix=suffix,
            development_host=source.get("TENANT_DEVELOPMENT_HOST", "localhost"),
            development_prefix=source.get("TENANT_DEVELOPMENT_PREFIX", "/t/"),
            trusted_proxy_cidrs=cidrs,
            https_enabled=source.get("TENANT_HTTPS_ENABLED", "true").lower() == "true",
            development_port=port,
        )


def _validate_dns_name(value: str, *, what: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 253:
        raise RoutingConfigurationError(f"{what} is invalid")
    if value.endswith("."):
        value = value[:-1]
    try:
        value.encode("ascii")
    except UnicodeEncodeError as exc:
        raise RoutingConfigurationError(f"{what} must be ASCII DNS") from exc
    value = value.lower()
    if value.startswith("*.") or any(not _LABEL.fullmatch(label) for label in value.split(".")):
        raise RoutingConfigurationError(f"{what} is invalid")
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return value
    raise RoutingConfigurationError(f"{what} cannot be an IP literal")


def _parse_authority(value: str, *, default_port: int | None) -> _Authority:
    """Parse one authority without repairing attacker-controlled syntax."""
    if not isinstance(value, str) or not value or len(value) > 255:
        raise ResolutionError("invalid authority")
    try:
        value.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ResolutionError("invalid authority") from exc
    if any(ord(char) <= 32 or ord(char) == 127 for char in value) or any(char in value for char in "@,/#\\%?"):
        raise ResolutionError("invalid authority")
    if value.count(":") > 1:
        raise ResolutionError("invalid authority")
    host, separator, supplied_port = value.partition(":")
    if separator:
        if not supplied_port.isascii() or not supplied_port.isdigit():
            raise ResolutionError("invalid authority")
        port = int(supplied_port)
        if not 1 <= port <= 65535:
            raise ResolutionError("invalid authority")
    else:
        port = None
    had_terminal_dot = host.endswith(".")
    if had_terminal_dot:
        host = host[:-1]
    canonical_host = _validate_dns_name_for_request(host)
    canonical_port = None if port == default_port else port
    return _Authority(
        canonical_host,
        canonical_port,
        value != canonical_host and not (value == f"{canonical_host}:{default_port}"),
    )


def _validate_dns_name_for_request(host: str) -> str:
    if not host or len(host) > 253:
        raise ResolutionError("invalid authority")
    host = host.lower()
    try:
        host.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ResolutionError("invalid authority") from exc
    if any(not _LABEL.fullmatch(label) for label in host.split(".")):
        raise ResolutionError("invalid authority")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return host
    raise ResolutionError("invalid authority")


def _raw_headers(headers: Iterable[tuple[bytes, bytes]]) -> dict[bytes, list[str]]:
    result: dict[bytes, list[str]] = {}
    for name, value in headers:
        lowered = name.lower()
        try:
            decoded = value.decode("latin-1")
        except UnicodeDecodeError as exc:  # defensive; latin-1 normally always decodes
            raise ResolutionError("invalid header") from exc
        result.setdefault(lowered, []).append(decoded)
    for name in (b"host", b":authority", b"forwarded", b"x-forwarded-host"):
        if len(result.get(name, ())) > 1:
            raise ResolutionError("ambiguous authority")
    return result


def _forwarded_host(value: str) -> str:
    if not value or "," in value or len(value) > 1024:
        raise ResolutionError("invalid forwarded authority")
    host: str | None = None
    for item in value.split(";"):
        key, equals, candidate = item.strip().partition("=")
        if not equals or not key or not candidate:
            raise ResolutionError("invalid forwarded authority")
        if key.lower() == "host":
            if host is not None or candidate.startswith('"') or candidate.endswith('"'):
                raise ResolutionError("invalid forwarded authority")
            host = candidate
    if host is None:
        raise ResolutionError("invalid forwarded authority")
    return host


def _peer_is_trusted(peer_ip: str | None, config: TenantRoutingConfig) -> bool:
    if not peer_ip:
        return False
    try:
        address = ipaddress.ip_address(peer_ip)
    except ValueError:
        return False
    return any(address in network for network in config.trusted_networks)


def extract_client_ip(headers: Iterable[tuple[bytes, bytes]], peer_ip: str | None, config: TenantRoutingConfig) -> str:
    """Use XFF only at the immediately trusted proxy boundary.

    A malformed chain is not reflected or partly trusted; its direct peer remains
    the rate-limit/audit key.  This preserves availability without accepting a
    client-provided spoofed address.
    """
    raw = _raw_headers(headers)
    if not _peer_is_trusted(peer_ip, config):
        return peer_ip or "0.0.0.0"
    values = raw.get(b"x-forwarded-for", ())
    if len(values) != 1:
        return peer_ip or "0.0.0.0"
    parts = [part.strip() for part in values[0].split(",")]
    if not parts or any(not part for part in parts):
        return peer_ip or "0.0.0.0"
    try:
        parsed = [ipaddress.ip_address(part) for part in parts]
    except ValueError:
        return peer_ip or "0.0.0.0"
    # A trusted immediate proxy supplied the chain.  The first hop is the client
    # address in the project's single trusted-Next topology.
    return str(parsed[0])


class TenantResolver:
    def __init__(self, config: TenantRoutingConfig, lookup: Callable[[str], str | None]) -> None:
        self.config = config
        self._lookup = lookup

    def _authority(self, headers: Iterable[tuple[bytes, bytes]], peer_ip: str | None) -> _Authority:
        raw = _raw_headers(headers)
        host_values, authority_values = raw.get(b"host", ()), raw.get(b":authority", ())
        if not host_values and not authority_values:
            raise ResolutionError("missing authority")
        direct: _Authority | None = None
        if host_values:
            direct = _parse_authority(host_values[0], default_port=443 if self.config.mode == "production" else self.config.development_port)
        if authority_values:
            parsed = _parse_authority(authority_values[0], default_port=443 if self.config.mode == "production" else self.config.development_port)
            if direct is not None and (direct.host, direct.port) != (parsed.host, parsed.port):
                raise ResolutionError("ambiguous authority")
            direct = parsed
        assert direct is not None
        if not _peer_is_trusted(peer_ip, self.config):
            return direct
        forwarded: list[str] = []
        if b"forwarded" in raw:
            forwarded.append(_forwarded_host(raw[b"forwarded"][0]))
        if b"x-forwarded-host" in raw:
            forwarded.append(raw[b"x-forwarded-host"][0])
        if not forwarded:
            return direct
        parsed_forwarded = [
            _parse_authority(value, default_port=443 if self.config.mode == "production" else self.config.development_port)
            for value in forwarded
        ]
        if len({(item.host, item.port) for item in parsed_forwarded}) != 1:
            raise ResolutionError("ambiguous forwarded authority")
        return parsed_forwarded[0]

    def prepare(self, *, path: str, raw_path: bytes, headers: Iterable[tuple[bytes, bytes]], peer_ip: str | None) -> _PreparedRoute:
        authority = self._authority(headers, peer_ip)
        if self.config.mode == "production":
            if authority.port is not None:
                raise ResolutionError("invalid port")
            suffix = "." + self.config.platform_suffix
            if not authority.host.endswith(suffix):
                raise ResolutionError("not a tenant host")
            slug = authority.host[: -len(suffix)]
            if not _SLUG.fullmatch(slug) or "." in slug or slug.lower() in RESERVED_SLUGS:
                raise ResolutionError("unroutable tenant")
            return _PreparedRoute(slug.lower(), path, authority.was_alias)
        if authority.host != self.config.development_host or authority.port is not None:
            raise ResolutionError("not the development host")
        prefix = self.config.development_prefix.encode("ascii")
        if not raw_path.startswith(prefix):
            raise ResolutionError("missing tenant prefix")
        remainder = raw_path[len(prefix):]
        slug_bytes, separator, child = remainder.partition(b"/")
        if not separator or not slug_bytes or b"%" in slug_bytes or b"\\" in slug_bytes:
            raise ResolutionError("malformed tenant prefix")
        try:
            slug = slug_bytes.decode("ascii").lower()
        except UnicodeDecodeError as exc:
            raise ResolutionError("malformed tenant prefix") from exc
        if not _SLUG.fullmatch(slug) or slug in RESERVED_SLUGS:
            raise ResolutionError("unroutable tenant")
        return _PreparedRoute(slug, "/" + child.decode("latin-1"), False)

    def resolve(self, authority: str, path: str, headers: Iterable[tuple[bytes, bytes]] = (), peer_ip: str | None = None) -> TenantRoute:
        # Public seam used by focused tests and non-ASGI callers.  The explicit
        # authority is installed as Host unless a raw Host is already supplied.
        supplied = list(headers)
        if not any(name.lower() == b"host" for name, _ in supplied):
            supplied.append((b"host", authority.encode("ascii")))
        prepared = self.prepare(path=path, raw_path=path.encode("latin-1"), headers=supplied, peer_ip=peer_ip)
        try:
            tenant_id = self._lookup(prepared.slug)
        except Exception as exc:
            raise ResolutionError("tenant unavailable") from exc
        if not tenant_id:
            raise ResolutionError("tenant unavailable")
        # A trial tenant deliberately routes here.  CS-061b's separate access-state
        # gate still rejects trial until its tracked policy reconciliation lands.
        return TenantRoute(str(tenant_id), prepared.slug, prepared.path)

    def resolve_prepared(self, prepared: _PreparedRoute) -> TenantRoute:
        try:
            tenant_id = self._lookup(prepared.slug)
        except Exception as exc:
            raise ResolutionError("tenant unavailable") from exc
        if not tenant_id:
            raise ResolutionError("tenant unavailable")
        return TenantRoute(str(tenant_id), prepared.slug, prepared.path)


def canonical_redirect(prepared: _PreparedRoute, config: TenantRoutingConfig, raw_path: bytes, query_string: bytes) -> RedirectResponse | None:
    if not prepared.was_alias or config.mode != "production":
        return None
    location = f"https://{prepared.slug}.{config.platform_suffix}{raw_path.decode('latin-1')}"
    if query_string:
        location += "?" + query_string.decode("latin-1")
    return RedirectResponse(location, status_code=308, headers={"Cache-Control": "no-store", "Vary": "Host"})


def canonical_origin(route: TenantRoute, config: TenantRoutingConfig) -> str:
    if config.mode == "production":
        return f"https://{route.slug}.{config.platform_suffix}"
    scheme = "https" if config.https_enabled else "http"
    port = f":{config.development_port}" if config.development_port else ""
    return f"{scheme}://{config.development_host}{port}"


def origin_is_canonical(value: str, expected_origin: str) -> bool:
    if not value or value == "null" or any(ord(char) <= 32 for char in value):
        return False
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        return False
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        return False
    return value.rstrip("/") == expected_origin


def request_has_access_cookie(headers: Iterable[tuple[bytes, bytes]]) -> bool:
    for name, value in headers:
        if name.lower() == b"cookie":
            try:
                pairs = value.decode("latin-1").split(";")
            except UnicodeDecodeError:
                return False
            if any(pair.strip().partition("=")[0] == "access_token" for pair in pairs):
                return True
    return False


class TenantRoutingMiddleware:
    """ASGI middleware which completes all edge checks before endpoint dependencies."""

    def __init__(self, app, *, resolver: TenantResolver, config: TenantRoutingConfig) -> None:
        self.app = app
        self.resolver = resolver
        self.config = config

    @staticmethod
    def _global(path: str) -> bool:
        return path == "/healthz" or path == "/invitations/accept" or path.startswith("/.well-known/")

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = scope.get("headers", ())
        peer = (scope.get("client") or (None, None))[0]
        try:
            scope["client_ip"] = extract_client_ip(headers, peer, self.config)
        except ResolutionError:
            scope["client_ip"] = peer or "0.0.0.0"
        path = scope.get("path", "")
        if self._global(path):
            await self.app(scope, receive, send)
            return
        try:
            prepared = self.resolver.prepare(
                path=path,
                raw_path=scope.get("raw_path") or path.encode("latin-1"),
                headers=headers,
                peer_ip=peer,
            )
        except ResolutionError:
            await PlainTextResponse("Not Found", status_code=404, headers={"Cache-Control": "no-store", "Vary": "Host"})(scope, receive, send)
            return
        if prepared.was_alias and self.config.mode == "production":
            if scope["method"] not in {"GET", "HEAD"}:
                await PlainTextResponse("Not Found", status_code=404, headers={"Cache-Control": "no-store", "Vary": "Host"})(scope, receive, send)
                return
            response = canonical_redirect(prepared, self.config, scope.get("raw_path") or path.encode("latin-1"), scope.get("query_string", b""))
            assert response is not None
            await response(scope, receive, send)
            return
        try:
            route = self.resolver.resolve_prepared(prepared)
        except ResolutionError:
            await PlainTextResponse("Not Found", status_code=404, headers={"Cache-Control": "no-store", "Vary": "Host"})(scope, receive, send)
            return
        # The development prefix is a routing selector, not part of the
        # application route.  Do this only after the canonical slug is resolved.
        # The development prefix is stripped before the application router sees
        # it.  Auth routes remain tenant-routed: their request body must not be
        # able to choose a tenant when the public topology already did.
        if self.config.mode == "development":
            scope = dict(scope)
            scope["path"] = route.path
            scope["raw_path"] = route.path.encode("latin-1")
        scope["tenant_route"] = route
        if scope["method"] in _UNSAFE_METHODS and request_has_access_cookie(headers):
            raw = _raw_headers(headers)
            origin = raw.get(b"origin", [None])[0]
            if origin is None:
                referer = raw.get(b"referer", [None])[0]
                origin = None if referer is None else _origin_from_referer(referer)
            if not origin_is_canonical(origin or "", canonical_origin(route, self.config)):
                await PlainTextResponse("Forbidden", status_code=403, headers={"Cache-Control": "no-store", "Vary": "Host"})(scope, receive, send)
                return
        await self.app(scope, receive, send)


def _origin_from_referer(referer: str) -> str | None:
    parsed = urlsplit(referer)
    if not parsed.scheme or not parsed.netloc or parsed.username or parsed.password:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"
