"""CS-036 / F36 — failing contract for the OpenTelemetry foundation.

Run with:
    python3 -m unittest coffee-scheduler/test/test_cs036_otel_foundation.py

This suite deliberately precedes the implementation.  It is red only because the
F36-owned ``otel/telemetry.py``, ``otel/requirements.txt``, and demo assets do not
exist yet; CS-038's Collector assets are intentionally treated as an external,
already-complete dependency.  Unit contracts never need Docker.  The two Collector
debug-output checks are explicitly Docker-gated integration contracts.
"""
from __future__ import annotations

import ast
import importlib.util
import inspect
import os
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OTEL_DIR = ROOT / "coffee-scheduler" / "otel"
TELEMETRY_PATH = OTEL_DIR / "telemetry.py"
REQUIREMENTS_PATH = OTEL_DIR / "requirements.txt"
DEMO_A_PATH = OTEL_DIR / "demo" / "service_a.py"
DEMO_B_PATH = OTEL_DIR / "demo" / "service_b.py"
RUN_DEMO_PATH = OTEL_DIR / "demo" / "run_demo.sh"

REQUIRED_PACKAGES = {
    "opentelemetry-api",
    "opentelemetry-sdk",
    "opentelemetry-exporter-otlp-proto-grpc",
    "opentelemetry-semantic-conventions",
}
REQUIRED_METRICS = {
    "solver.solve_time_ms",
    "rule_eval.count",
    "forecast.quantile_p70",
    "a2a.server.duration_ms",
    "a2a.calls",
}


def _docker_available() -> bool:
    return bool(shutil.which("docker")) and subprocess.run(
        ("docker", "info"), capture_output=True, text=True
    ).returncode == 0


class OtelFoundationContractTests(unittest.TestCase):
    """Static and unit contracts; all are deterministic and Docker-free."""

    def setUp(self) -> None:
        # One explicit precondition documents the expected red state without making
        # a local OTel installation, a Collector, or the network a unit-test input.
        for asset in (TELEMETRY_PATH, REQUIREMENTS_PATH, DEMO_A_PATH, DEMO_B_PATH, RUN_DEMO_PATH):
            self.assertTrue(asset.is_file(), f"F36 implementation asset is absent: {asset.relative_to(ROOT)}")
        self.telemetry_source = TELEMETRY_PATH.read_text()
        self.demo_a_source = DEMO_A_PATH.read_text()
        self.demo_b_source = DEMO_B_PATH.read_text()

    def _load_telemetry(self):
        spec = importlib.util.spec_from_file_location("cs036_telemetry", TELEMETRY_PATH)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module

    # spec: F36.ac1 — all four OTel dependencies are exact version pins.
    def test_requirements_pin_the_complete_sdk_set(self) -> None:
        pins = {
            line.split("==", 1)[0]
            for line in REQUIREMENTS_PATH.read_text().splitlines()
            if line and not line.startswith("#") and "==" in line
        }
        self.assertEqual(REQUIRED_PACKAGES, pins)

    # spec: F36.ac2 — resource schema carries the three shared agent keys.
    def test_resource_schema_has_the_shared_agent_keys(self) -> None:
        tree = ast.parse(self.telemetry_source)
        literals = {node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)}
        self.assertTrue({"service.name", "service.version", "service.namespace"}.issubset(literals))

    # spec: F36.ac3 — no anonymous/unknown-service telemetry starts.
    def test_init_rejects_a_blank_service_name(self) -> None:
        telemetry = self._load_telemetry()
        with self.assertRaises((TypeError, ValueError)):
            telemetry.init_telemetry(" ")

    # spec: F36.ac4 — deployment environment belongs to Collector resource processing.
    def test_sdk_resource_does_not_own_deployment_environment(self) -> None:
        self.assertNotIn("deployment.environment", self.telemetry_source)

    # spec: F36.ac5 — tenant identity is span-level, not process-level resource metadata.
    def test_tenant_id_is_not_a_resource_attribute(self) -> None:
        resource_block = self.telemetry_source.split("Resource", 1)[-1].split("TracerProvider", 1)[0]
        self.assertNotIn("tenant_id", resource_block)

    # spec: F36.ac6 — employee names and emails are never telemetry attributes.
    def test_demo_does_not_emit_employee_pii_attributes(self) -> None:
        combined = self.telemetry_source + self.demo_a_source + self.demo_b_source
        self.assertNotRegex(combined, r"(?i)(employee[_.-]?(name|email)|email[_.-]?address)")

    # spec: F36.ac7 — keep all head spans for the Collector's tail sampler.
    def test_sampler_is_parent_based_always_on(self) -> None:
        self.assertRegex(self.telemetry_source, r"ParentBased\s*\(\s*ALWAYS_ON\s*\)")

    # spec: F36.ac8 — both standard W3C propagators are installed globally.
    def test_propagator_is_tracecontext_and_baggage(self) -> None:
        self.assertIn("tracecontext,baggage", self.telemetry_source.replace(" ", ""))

    # spec: F36.ac9 — local gRPC export is plaintext and configurable.
    def test_otlp_grpc_exporter_is_local_and_insecure(self) -> None:
        self.assertRegex(
            self.telemetry_source,
            r"OTEL_EXPORTER_OTLP_ENDPOINT.*localhost:4317|localhost:4317.*OTEL_EXPORTER_OTLP_ENDPOINT",
        )

    # spec: F36.ac10 — shutdown synchronously drains short-lived-process telemetry.
    def test_shutdown_force_flushes_before_provider_shutdown(self) -> None:
        tree = ast.parse(self.telemetry_source)
        shutdown = ast.get_source_segment(self.telemetry_source, next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "shutdown"
        ))
        flush_positions = [match.start() for match in re.finditer(r"\.force_flush\(\)", shutdown)]
        provider_shutdown_positions = [
            match.start() for match in re.finditer(r"_\w+_provider\.shutdown\(\)", shutdown)
        ]
        self.assertLess(max(flush_positions), min(provider_shutdown_positions))

    # spec: F36.ac10 — repeated explicit/atexit shutdown never flushes after a provider closes.
    def test_shutdown_idempotence_guard_precedes_flush(self) -> None:
        tree = ast.parse(self.telemetry_source)
        shutdown = ast.get_source_segment(self.telemetry_source, next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "shutdown"
        ))
        self.assertLess(shutdown.index("if _shutdown_done:"), shutdown.index(".force_flush()"))

    # spec: F36.ac11 — callers receive the reusable tracer/meter pair.
    def test_init_telemetry_has_the_stable_public_signature(self) -> None:
        telemetry = self._load_telemetry()
        signature = inspect.signature(telemetry.init_telemetry)
        self.assertEqual(["service_name", "service_version"], list(signature.parameters))

    # spec: F36.ac11 — the happy path supplies both instrument factories to an agent.
    def test_init_telemetry_returns_a_tracer_and_meter_pair(self) -> None:
        telemetry = self._load_telemetry()
        self.assertIsInstance(telemetry.init_telemetry("scheduler"), tuple)

    # spec: F36.ac12 — caller helper writes W3C headers into its supplied carrier.
    def test_inject_headers_mutates_and_returns_the_header_carrier(self) -> None:
        telemetry = self._load_telemetry()
        headers = {}
        self.assertIs(headers, telemetry.inject_headers(headers))

    # spec: F36.ac13 — valid inbound context keeps its existing trace ID.
    def test_extract_context_preserves_the_incoming_trace_id(self) -> None:
        telemetry = self._load_telemetry()
        trace_id = "0af7651916cd43dd8448eb211c80319c"
        context = telemetry.extract_context({"traceparent": f"00-{trace_id}-b7ad6b7169203331-01"})
        from opentelemetry import trace

        self.assertEqual(trace_id, f"{trace.get_current_span(context).get_span_context().trace_id:032x}")

    # spec: F36.ac14 — the reference harness creates a client/server trace over stdlib HTTP.
    def test_demo_uses_stdlib_http_client_and_server_with_client_server_spans(self) -> None:
        combined = self.demo_a_source + self.demo_b_source
        self.assertRegex(combined, r"urllib\.request.*http\.server|http\.server.*urllib\.request")

    # spec: F36.ac15 — every named shared metric is part of the bootstrap vocabulary.
    def test_metric_vocabulary_is_complete(self) -> None:
        self.assertTrue(REQUIRED_METRICS.issubset(set(re.findall(r"[a-z0-9_.]+", self.telemetry_source))))


@unittest.skipUnless(
    os.environ.get("CS036_RUN_COLLECTOR_TESTS") == "1" and _docker_available(),
    "set CS036_RUN_COLLECTOR_TESTS=1 with Docker to run Collector debug verification",
)
class OtelFoundationCollectorIntegrationTests(unittest.TestCase):
    """AC16–AC17 are intentionally Docker-gated; they never start a shared Compose stack."""

    def setUp(self) -> None:
        for asset in (TELEMETRY_PATH, REQUIREMENTS_PATH, DEMO_A_PATH, DEMO_B_PATH, RUN_DEMO_PATH):
            self.assertTrue(asset.is_file(), f"F36 implementation asset is absent: {asset.relative_to(ROOT)}")

    def _run_demo(self) -> subprocess.CompletedProcess[str]:
        env = os.environ | {"OTEL_EXPORTER_OTLP_ENDPOINT": "localhost:4317"}
        return subprocess.run(("sh", str(RUN_DEMO_PATH)), capture_output=True, text=True, env=env)

    # spec: F36.ac16 — Collector debug evidence contains matching CLIENT/SERVER trace IDs.
    def test_run_demo_asserts_a_single_client_server_trace_id_in_collector_debug_output(self) -> None:
        result = self._run_demo()
        self.assertEqual(0, result.returncode, result.stderr)

    # spec: F36.ac17 — Collector debug evidence includes at least one required A2A RED metric.
    def test_run_demo_asserts_an_a2a_metric_in_collector_debug_output(self) -> None:
        result = self._run_demo()
        self.assertRegex(result.stdout + result.stderr, r"a2a\.(calls|server\.duration_ms)")


if __name__ == "__main__":
    unittest.main()
