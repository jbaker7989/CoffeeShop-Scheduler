"""Check external Python call sites against the CS-041 inventory.

Recognized capability calls and references are checked, including direct
aliases. An exact AST fingerprint in the inventory survives line shifts and
formatting changes, but changing or copying the call requires review.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = "scripts/ci/observability_boundaries.json"
CONTRACT = "test/test_cs041_observability.py"

# SDK namespaces that can acquire or perform external operations. Pure model,
# error, and request-construction modules in these packages are listed below.
SDK_ROOTS = {
    "HTTP": ("httpx", "httpcore", "requests", "urllib.request", "urllib3", "http.client", "aiohttp", "socket", "anthropic"),
    "A2A": ("a2a",),
    "HTTP server": ("fastapi.FastAPI", "starlette.applications.Starlette", "http.server.HTTPServer"),
    "DB": ("psycopg", "psycopg2", "psycopg_pool", "asyncpg", "sqlite3", "sqlalchemy",
           "alembic.op", "langgraph.checkpoint.postgres"),
    "tool": ("langchain_core.tools", "langgraph.prebuilt.ToolNode", "mcp"),
}
PURE_PREFIXES = (
    "a2a.types", "psycopg.sql", "psycopg.rows", "psycopg.types", "psycopg.pq", "psycopg.errors",
    "sqlalchemy.pool", "sqlalchemy.text", "langchain_core.tools.StructuredTool.from_function",
    "urllib.request.Request", "httpx.Request", "httpx.Response", "httpx.Headers",
    "httpx.HTTPError", "httpx.TimeoutException", "httpx.NetworkError", "httpx.RemoteProtocolError",
    "anthropic.APIError",
)
PURE_OPERATIONS = {
    "psycopg.Error", "psycopg.DatabaseError", "psycopg.OperationalError", "psycopg.IntegrityError",
    "socket.AF_INET", "socket.SOCK_STREAM",
}
RAW_METHODS = {
    "DB": frozenset({"execute", "executemany", "exec_driver_sql", "callproc", "connect", "connection",
                     "commit", "rollback"}),
    "A2A": frozenset({"send_message", "send_message_streaming", "get_task", "cancel_task"}),
    "tool": frozenset({"invoke", "ainvoke", "call_tool", "run_tool"}),
}
HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options", "request", "send",
                          "stream", "urlopen"})
HTTP_CLIENT_FACTORIES = frozenset({
    "httpx.Client", "httpx.AsyncClient", "requests.Session", "requests.sessions.Session",
    "urllib.request.build_opener", "urllib3.PoolManager", "aiohttp.ClientSession",
    "http.client.HTTPConnection", "http.client.HTTPSConnection", "anthropic.Anthropic", "anthropic.AsyncAnthropic",
})
DYNAMIC = frozenset({"getattr", "vars", "eval", "exec", "globals", "locals", "__import__", "import_module",
                     "attrgetter", "methodcaller"})


def under(name: str, prefixes: tuple[str, ...]) -> bool:
    return any(name == prefix or name.startswith(prefix + ".") for prefix in prefixes)


def imported_capability(name: str) -> str | None:
    if name in PURE_OPERATIONS or under(name, PURE_PREFIXES):
        return None
    for category, prefixes in SDK_ROOTS.items():
        if under(name, prefixes):
            return category
    return None


def reference(node: ast.AST | None) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{reference(node.value)}.{node.attr}"
    return ""


def imports(tree: ast.AST) -> dict[str, set[str]]:
    bindings = defaultdict(set)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for entry in node.names:
                name = entry.asname or entry.name.split(".")[0]
                bindings[name].add(entry.name if entry.asname else name)
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            for entry in node.names:
                bindings[entry.asname or entry.name].add(f"{node.module}.{entry.name}")
    return bindings


def resolve(name: str, bindings: dict[str, set[str]]) -> set[str]:
    root, sep, suffix = name.partition(".")
    resolved = {binding + sep + suffix for binding in bindings.get(root, ())}
    resolved.update(bindings.get(name, ()))
    return resolved


def capability(node: ast.AST, bindings: dict[str, set[str]]) -> str | None:
    name = reference(node)
    if not name:
        return None
    resolved = resolve(name, bindings)
    resolved.add(name)
    for candidate in sorted(resolved):
        imported = imported_capability(candidate)
        if imported:
            return imported
        method = candidate.rsplit(".", 1)[-1]
        if "." in candidate:
            for category, methods in RAW_METHODS.items():
                if method in methods:
                    return category
            receiver = candidate.rsplit(".", 2)[-2]
            if method in HTTP_METHODS and receiver.endswith(("client", "session", "transport", "opener", "http", "peer")):
                return "HTTP"
            if method == "create" and receiver in {"messages", "completions", "responses"}:
                return "HTTP"
            if method == "copy" and receiver.endswith(("cur", "cursor")):
                return "DB"
        if candidate.removeprefix("builtins.").removeprefix("importlib.").removeprefix("operator.") in DYNAMIC:
            return "dynamic"
    return None


def http_client_factory(node: ast.AST, bindings: dict[str, set[str]]) -> bool:
    if not isinstance(node, ast.Call):
        return False
    name = reference(node.func)
    return bool({name, *resolve(name, bindings)} & HTTP_CLIENT_FACTORIES)


def aliases(tree: ast.AST, bindings: dict[str, set[str]]) -> None:
    # Track aliases through direct assignments conservatively across scopes.
    # Also flag capability references used as values, even before they are called.
    assignments = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)) and node.value is not None:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            assignments.extend((reference(target), node.value) for target in targets if reference(target))
        if isinstance(node, ast.withitem) and (target := reference(node.optional_vars)):
            assignments.append((target, node.context_expr))
    changed = True
    while changed:
        changed = False
        for target, value in assignments:
            if isinstance(value, ast.Call) and http_client_factory(value, bindings):
                value = value.func
            category = capability(value, bindings)
            source = reference(value)
            if source and (category or source in bindings):
                # Retain the qualified operation across aliases, including nested imports.
                inherited = {source, *resolve(source, bindings)}
                if not inherited <= bindings[target]:
                    if len(inherited | bindings[target]) > 32:
                        raise ValueError(f"cannot resolve capability alias {target!r}; use an explicit adapter")
                    bindings[target].update(inherited)
                    changed = True


def dynamic_access(node: ast.Call, bindings: dict[str, set[str]]) -> bool:
    name = reference(node.func)
    names = {name, *resolve(name, bindings)}
    names = {item.removeprefix("builtins.").removeprefix("importlib.") for item in names}
    if names & {"__import__", "import_module"} and node.args:
        module = node.args[0]
        if isinstance(module, ast.Constant) and isinstance(module.value, str):
            return imported_capability(module.value) is not None or any(
                under(prefix, (module.value,)) for prefixes in SDK_ROOTS.values() for prefix in prefixes
            )
    if "getattr" in names and len(node.args) >= 2:
        member = node.args[1]
        if isinstance(member, ast.Constant) and isinstance(member.value, str):
            return (
                member.value.startswith("__")
                or member.value in HTTP_METHODS
                or any(member.value in methods for methods in RAW_METHODS.values())
                or capability(node.args[0], bindings) is not None
            )
    return True


@dataclass(frozen=True)
class Site:
    module: str
    key: str
    line: int

    @property
    def location(self) -> str:
        return f"{self.module}:{self.line}"


def inspect_source(module: str, source: str) -> list[Site]:
    tree = ast.parse(source, filename=module)
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    bindings = imports(tree)
    aliases(tree, bindings)
    sites = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and any(entry.name == "*" for entry in node.names):
            category = imported_capability(node.module or "")
        elif isinstance(node, (ast.Name, ast.Attribute)) and isinstance(node.ctx, ast.Load):
            parent = parents[node]
            reflective = isinstance(node, ast.Attribute) and node.attr in {"__dict__", "__getattribute__"}
            if isinstance(parent, ast.Attribute) and parent.value is node and not reflective:
                continue  # Classify the complete attribute chain.
            category = capability(node, bindings)
            if reflective:
                category = "dynamic"
            if isinstance(node, ast.Attribute) and node.attr == "__call__":
                category = capability(node.value, bindings) or category
            if isinstance(node, ast.Attribute) and http_client_factory(node.value, bindings):
                category = "HTTP"
            if isinstance(parent, ast.Call) and parent.func is node:
                node = parent
                if category == "dynamic" and not dynamic_access(node, bindings):
                    outer = parents[node]
                    if not (isinstance(outer, ast.Call) and outer.func is node):
                        continue
                first = node.args[0] if node.args else None
                if (
                    category is None and isinstance(node.func, ast.Attribute) and node.func.attr in HTTP_METHODS
                    and isinstance(first, ast.Constant) and isinstance(first.value, str)
                    and first.value.startswith(("http://", "https://"))
                ):
                    category = "HTTP"
        else:
            continue
        if category is None:
            continue

        scope = []
        owner = parents.get(node)
        while owner is not None:
            if isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                scope.append(owner.name)
            owner = parents.get(owner)
        symbol = ".".join(reversed(scope)) or "<module>"
        operation = reference(node.func if isinstance(node, ast.Call) else node) or type(node).__name__
        origin = sorted(resolve(operation, bindings))
        digest = hashlib.sha256(f"{ast.dump(node)}\n{origin}".encode()).hexdigest()[:20]
        sites.append(Site(module, f"{symbol}|{category}|{operation}|{digest}", node.lineno))
    return sorted(sites, key=lambda site: (site.line, site.key))


def project_sources(root: Path) -> dict[str, str]:
    result = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard", "-z", "--", "*.py"],
        cwd=root, capture_output=True, check=True,
    )
    sources = {}
    for module in sorted({os.fsdecode(path) for path in result.stdout.split(b"\0") if path}):
        if {"test", "tests"} & set(Path(module).parts):
            continue  # The policy applies to shipped code, not fake external calls in tests.
        path = root / module
        if path.is_file():
            sources[module] = path.read_text(encoding="utf-8")
    return sources


def evidence(test_tree: ast.Module) -> dict[str, set[str]]:
    cases = {}
    for cls in (item for item in test_tree.body if isinstance(item, ast.ClassDef)):
        for method in (item for item in cls.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))):
            cases[f"{cls.name}.{method.name}"] = {
                node.args[0].value for node in ast.walk(method)
                if isinstance(node, ast.Call) and reference(node.func) == "self.assert_boundary"
                and node.args and isinstance(node.args[0], ast.Constant)
            }
    return cases


def defined_symbol(source: str, symbol: str) -> bool:
    node = ast.parse(source)
    for part in symbol.split("."):
        node = next((item for item in node.body if isinstance(
            item, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ) and item.name == part), None)
        if node is None:
            return False
    return True


def check(root: Path, *, manifest: dict | None = None, extra_sources: dict[str, str] | None = None) -> list[str]:
    manifest = json.loads((root / REGISTRY).read_text()) if manifest is None else manifest
    sources = project_sources(root)
    sources.update(extra_sources or {})
    tests = evidence(ast.parse((root / CONTRACT).read_text()))
    diagnostics = []
    approved = Counter()
    boundaries = manifest["boundaries"]
    if {entry["category"] for entry in boundaries.values()} != {"HTTP", "A2A", "DB", "tool"}:
        diagnostics.append("registry must cover HTTP, A2A, DB, and tool boundaries")
    for key, entry in sorted(boundaries.items()):
        module, symbol, test = entry["module"], entry["symbol"], entry["test"]
        if (
            module not in sources or not defined_symbol(sources[module], symbol)
            or not entry.get("span") or entry.get("kind") not in {"CLIENT", "SERVER", "INTERNAL"}
        ):
            diagnostics.append(f"{key}: missing boundary or span identity at {module}:{symbol}")
            continue
        if key not in tests.get(test, ()) or not test.split(".")[-1].startswith("test_"):
            diagnostics.append(f"{key}: {test} must assert the registered boundary")
            continue
        for site, count in entry["sites"].items():
            if site.partition("|")[0] == symbol:
                approved[module, site] += count
            else:
                diagnostics.append(f"{key}: registered raw site is outside {module}:{symbol}")
    for module, entry in sorted(manifest["exemptions"].items()):
        if not entry.get("owner", "").strip() or not entry.get("reason", "").strip():
            diagnostics.append(f"{module}: each exemption needs an owner and reason")
            continue
        for site, count in entry["sites"].items():
            approved[module, site] += count

    for module, source in sorted(sources.items()):
        for site in inspect_source(module, source):
            identity = site.module, site.key
            if approved[identity] > 0:
                approved[identity] -= 1
            else:
                symbol, category, operation, _digest = site.key.split("|")
                diagnostics.append(f"{site.location}: unregistered {category} site {symbol} ({operation}); "
                                   f"use a tested adapter or review fingerprint {site.key}")
    diagnostics.extend(f"{module}: stale inventory site {site}" for (module, site), count in sorted(approved.items())
                       if count)
    return diagnostics


def main() -> int:
    try:
        diagnostics = check(ROOT)
    except (OSError, ValueError, SyntaxError, KeyError, TypeError, subprocess.CalledProcessError) as error:
        diagnostics = [str(error)]
    if diagnostics:
        for item in diagnostics:
            print(f"FAIL — {item}", file=sys.stderr)
        return 1
    print("PASS — CS-041 external call sites match the tested boundaries and reviewed exemptions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
