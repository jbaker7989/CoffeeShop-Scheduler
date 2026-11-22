"""CS-038 / F38 — OTel Collector deployment: spec-derived test suite.

Run with: python3 -m unittest coffee-scheduler/test/test_cs038_otel_collector.py

SDD/TDD: these tests are written from the F38 acceptance criteria *before* the
production assets exist. With ``otel/collector-config.yaml`` and
``docker-compose.observability.yml`` absent, the static tests fail at
``_load_config`` / ``_load_compose`` (FileNotFoundError) — the documented red state
for "absent collector assets". The ``validate`` test additionally proves an *invalid*
config is rejected by the pinned image.

The Docker-gated tests (AC10/AC11) run the collector in full isolation via ``docker
run`` with ephemeral host ports and tear the container down in a ``finally`` block, so
they never touch a developer's data-plane ``docker compose up`` stack.
"""
import datetime
import json
import shutil
import subprocess
import tempfile
import time
import unittest
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "coffee-scheduler" / "otel" / "collector-config.yaml"
COMPOSE_PATH = ROOT / "docker-compose.observability.yml"
# BUG-055: the separate production TLS/mTLS Collector configuration + overlay.
PROD_CONFIG_PATH = ROOT / "coffee-scheduler" / "otel" / "collector-config.prod.yaml"
PROD_COMPOSE_PATH = ROOT / "docker-compose.observability.prod.yml"
# PEM markers that must never appear committed in config/compose (no committed secrets).
PEM_MARKERS = ("BEGIN CERTIFICATE", "BEGIN PRIVATE KEY", "BEGIN RSA PRIVATE KEY", "BEGIN EC PRIVATE KEY")


def _server_tls_mode(tls_block: object) -> str:
    """Classify an OTLP receiver ``tls`` block: 'disabled' | 'tls' | 'mtls'.

    Raises ``ValueError`` for an incomplete server pair (cert without key or key
    without cert) — the fail-closed case BUG-055 AC13 requires the config to avoid.
    """
    if not tls_block:
        return "disabled"
    cert = tls_block.get("cert_file")
    key = tls_block.get("key_file")
    if bool(cert) != bool(key):
        raise ValueError("server TLS requires both cert_file and key_file")
    if not cert:
        return "disabled"
    return "mtls" if tls_block.get("client_ca_file") else "tls"

# The pin under test. Exact (non-latest) contrib tag, released 2026-10-27 — AC9 asserts
# it is at least two weeks old against this known release date (no network required).
PINNED_IMAGE = "otel/opentelemetry-collector-contrib:0.164.0"
PINNED_RELEASE_DATE = datetime.date(2026, 10, 27)
COLLECTOR_SERVICE = "otel-collector"
FORBIDDEN_HEALTHCHECK_TOKENS = ("sh", "bash", "curl", "wget")


def _load_config() -> dict:
    with CONFIG_PATH.open() as handle:
        return yaml.safe_load(handle)


def _load_compose() -> dict:
    with COMPOSE_PATH.open() as handle:
        return yaml.safe_load(handle)


def _base_type(exporter_name: str) -> str:
    """OTel component aliases are ``<type>/<name>``; the type is the prefix."""
    return exporter_name.split("/", 1)[0]


def _endpoint_host_port(value: str) -> str:
    """Normalise ``host:port`` / ``http://host:port/path`` to the ``host:port`` core."""
    core = value.split("//", 1)[-1]
    return core.split("/", 1)[0]


class CollectorConfigStaticTests(unittest.TestCase):
    """AC1–AC7: the committed collector config is the contract (no Docker needed)."""

    def setUp(self) -> None:
        self.config = _load_config()
        self.pipelines = self.config["service"]["pipelines"]

    # spec: F38.ac1 — OTLP receivers bound to 4317 (gRPC) and 4318 (HTTP) on 0.0.0.0.
    def test_otlp_receivers_bind_4317_grpc_and_4318_http(self) -> None:
        otlp = self.config["receivers"]["otlp"]["protocols"]
        self.assertEqual("0.0.0.0:4317", _endpoint_host_port(otlp["grpc"]["endpoint"]))
        self.assertEqual("0.0.0.0:4318", _endpoint_host_port(otlp["http"]["endpoint"]))

    # spec: F38.ac2 — tail sampling keeps 100% of errors and samples the rest at 10%.
    def test_tail_sampling_keeps_errors_and_samples_rest_at_ten_percent(self) -> None:
        policies = self.config["processors"]["tail_sampling"]["policies"]
        status_policies = [p for p in policies if p.get("type") == "status_code"]
        prob_policies = [p for p in policies if p.get("type") == "probabilistic"]
        self.assertTrue(
            any("ERROR" in p["status_code"]["status_codes"] for p in status_policies),
            "no status_code policy retaining ERROR traces (100% of errors)",
        )
        self.assertTrue(
            any(p["probabilistic"]["sampling_percentage"] == 10 for p in prob_policies),
            "no probabilistic policy at 10% for the remaining traces",
        )

    # spec: F38.ac3 — traces processor order: memory_limiter first, tail_sampling before batch, batch last.
    def test_traces_processor_order_is_load_bearing(self) -> None:
        processors = self.pipelines["traces"]["processors"]
        self.assertEqual("memory_limiter", processors[0])
        self.assertEqual("batch", processors[-1])
        self.assertLess(
            processors.index("tail_sampling"),
            processors.index("batch"),
            "tail_sampling must decide on whole traces before batch",
        )

    # spec: F38.ac4 — resource processor upserts deployment.environment; the three keys are documented.
    def test_resource_processor_sets_documented_attributes(self) -> None:
        attributes = self.config["processors"]["resource"]["attributes"]
        by_key = {a["key"]: a for a in attributes}
        self.assertEqual(
            {"service.name", "service.version", "deployment.environment"},
            set(by_key),
        )
        self.assertEqual("upsert", by_key["deployment.environment"]["action"])

    # spec: F38.ac5 — every pipeline has debug; traces→otlp(Tempo), metrics→prometheusremotewrite(Mimir), logs→otlphttp(Loki).
    def test_pipelines_export_debug_plus_typed_backends(self) -> None:
        exporters = self.config["exporters"]
        for pipeline in ("traces", "metrics", "logs"):
            self.assertIn("debug", self.pipelines[pipeline]["exporters"], pipeline)

        traces_backends = [e for e in self.pipelines["traces"]["exporters"] if e != "debug"]
        metrics_backends = [e for e in self.pipelines["metrics"]["exporters"] if e != "debug"]
        logs_backends = [e for e in self.pipelines["logs"]["exporters"] if e != "debug"]

        self.assertTrue(any(_base_type(e) == "otlp" for e in traces_backends), traces_backends)
        self.assertTrue(
            any(_base_type(e) == "prometheusremotewrite" for e in metrics_backends),
            metrics_backends,
        )
        self.assertTrue(any(_base_type(e) == "otlphttp" for e in logs_backends), logs_backends)

        # The typed backends point at Tempo / Mimir / Loki respectively.
        traces_ep = exporters[traces_backends[0]]["endpoint"]
        metrics_ep = exporters[metrics_backends[0]]["endpoint"]
        logs_ep = exporters[logs_backends[0]]["endpoint"]
        self.assertIn("tempo", traces_ep)
        self.assertIn("mimir", metrics_ep)
        self.assertIn("loki", logs_ep)

    # spec: F38.ac6 — each backend exporter is non-fatal (retry_on_failure enabled) so an absent backend can't crash it.
    def test_backend_exporters_are_non_fatal(self) -> None:
        exporters = self.config["exporters"]
        backend_names = [name for name in exporters if name != "debug"]
        self.assertTrue(backend_names, "no backend exporters declared")
        for name in backend_names:
            retry = exporters[name].get("retry_on_failure")
            self.assertIsNotNone(retry, f"{name} missing retry_on_failure")
            self.assertTrue(retry.get("enabled"), f"{name} retry_on_failure not enabled")

    # spec: F38.ac7 — health_check extension binds 0.0.0.0:13133 and is enabled in service.extensions.
    def test_health_check_extension_binds_13133(self) -> None:
        endpoint = self.config["extensions"]["health_check"]["endpoint"]
        self.assertEqual("0.0.0.0:13133", _endpoint_host_port(endpoint))
        self.assertIn("health_check", self.config["service"]["extensions"])


class CollectorComposeStaticTests(unittest.TestCase):
    """AC8–AC9: the additive Compose service pins the image and avoids a shell healthcheck."""

    def setUp(self) -> None:
        self.compose = _load_compose()
        self.service = self.compose["services"][COLLECTOR_SERVICE]

    # spec: F38.ac8 — distroless collector service defines no sh/bash/curl/wget healthcheck.
    def test_collector_has_no_shell_or_curl_healthcheck(self) -> None:
        healthcheck = self.service.get("healthcheck")
        if healthcheck is None:
            return  # No healthcheck is the correct distroless default.
        test_tokens = " ".join(healthcheck.get("test", [])).lower()
        for token in FORBIDDEN_HEALTHCHECK_TOKENS:
            self.assertNotIn(token, test_tokens, f"distroless healthcheck must not use {token!r}")

    # spec: F38.ac9 — image pinned to an exact contrib tag at least two weeks old.
    def test_image_is_pinned_and_at_least_two_weeks_old(self) -> None:
        image = self.service["image"]
        self.assertEqual(PINNED_IMAGE, image)
        repo, _, tag = image.rpartition(":")
        self.assertEqual("otel/opentelemetry-collector-contrib", repo)
        self.assertNotIn(tag, ("", "latest"))
        self.assertRegex(tag, r"^\d+\.\d+\.\d+$")
        age_days = (datetime.date.today() - PINNED_RELEASE_DATE).days
        self.assertGreaterEqual(age_days, 14, f"pinned tag only {age_days} days old (<2 weeks)")

    # spec: F38.ac1 — the Compose service publishes the three OTLP/health ports it binds.
    # BUG-055 added a host-IP prefix, so match on the CONTAINER port (last colon field).
    def test_compose_publishes_otlp_and_health_ports(self) -> None:
        published = {str(p).rsplit(":", 1)[-1] for p in self.service["ports"]}
        self.assertTrue({"4317", "4318", "13133"}.issubset(published), self.service["ports"])

    # spec: F38.ac12 — every published development host port binds 127.0.0.1 only, so the
    # plaintext, unauthenticated dev Collector is never exposed to the LAN (BUG-055).
    def test_development_host_ports_are_loopback_only(self) -> None:
        expected = {
            "127.0.0.1:4317:4317",
            "127.0.0.1:4318:4318",
            "127.0.0.1:13133:13133",
        }
        published = {str(p) for p in self.service["ports"]}
        self.assertEqual(expected, published, self.service["ports"])

    # spec: F38.ac12 — the in-container receivers stay on 0.0.0.0 so other Compose
    # services can reach otel-collector:4317/4318 over the compose network.
    def test_container_receivers_stay_on_all_interfaces(self) -> None:
        otlp = _load_config()["receivers"]["otlp"]["protocols"]
        self.assertEqual("0.0.0.0:4317", _endpoint_host_port(otlp["grpc"]["endpoint"]))
        self.assertEqual("0.0.0.0:4318", _endpoint_host_port(otlp["http"]["endpoint"]))


class ProductionCollectorConfigTests(unittest.TestCase):
    """AC13 (BUG-055): the production config terminates TLS and commits no secrets."""

    def setUp(self) -> None:
        with PROD_CONFIG_PATH.open() as handle:
            self.config = yaml.safe_load(handle)
        self.prod_text = PROD_CONFIG_PATH.read_text()
        self.protocols = self.config["receivers"]["otlp"]["protocols"]

    # spec: F38.ac13 — the server TLS classifier fails closed on an incomplete pair,
    # and reports plain TLS vs. mTLS by whether a client CA is configured.
    def test_server_tls_mode_classifier_is_fail_closed(self) -> None:
        self.assertEqual("disabled", _server_tls_mode(None))
        self.assertEqual("tls", _server_tls_mode({"cert_file": "c", "key_file": "k"}))
        self.assertEqual(
            "mtls",
            _server_tls_mode({"cert_file": "c", "key_file": "k", "client_ca_file": "ca"}),
        )
        with self.assertRaises(ValueError):
            _server_tls_mode({"cert_file": "c"})  # key missing → rejected
        with self.assertRaises(ValueError):
            _server_tls_mode({"key_file": "k"})  # cert missing → rejected

    # spec: F38.ac13 — both receivers terminate TLS (cert_file + key_file configured).
    def test_both_receivers_terminate_tls(self) -> None:
        for proto in ("grpc", "http"):
            tls = self.protocols[proto].get("tls")
            self.assertIsNotNone(tls, f"{proto} receiver has no tls block")
            self.assertTrue(tls.get("cert_file"), f"{proto} tls missing cert_file")
            self.assertTrue(tls.get("key_file"), f"{proto} tls missing key_file")

    # spec: F38.ac13 — plaintext is never selected on a production receiver.
    def test_production_receivers_do_not_select_plaintext(self) -> None:
        for proto in ("grpc", "http"):
            tls = self.protocols[proto].get("tls", {})
            self.assertNotEqual(True, tls.get("insecure"), f"{proto} selects plaintext")
            # And the mode is a real TLS mode, not disabled.
            self.assertIn(_server_tls_mode(tls), ("tls", "mtls"))

    # spec: F38.ac13 — client authentication (mTLS) is enabled only when a client CA
    # is explicitly configured; this production config opts in via env substitution.
    def test_client_auth_enabled_only_with_client_ca(self) -> None:
        for proto in ("grpc", "http"):
            tls = self.protocols[proto]["tls"]
            self.assertEqual("mtls", _server_tls_mode(tls))
            self.assertTrue(tls.get("client_ca_file"))
        # Dropping the client CA must downgrade to plain TLS (not stay mTLS) — proves
        # the opt-in semantics rather than an unconditional client-auth requirement.
        no_ca = {k: v for k, v in self.protocols["grpc"]["tls"].items() if k != "client_ca_file"}
        self.assertEqual("tls", _server_tls_mode(no_ca))

    # spec: F38.ac13 — no certificate or private-key material is committed; TLS file
    # locations are env placeholders / mounted paths, not inline PEM blobs.
    def test_production_config_commits_no_secret_material(self) -> None:
        for marker in PEM_MARKERS:
            self.assertNotIn(marker, self.prod_text, f"committed PEM material: {marker}")
        for proto in ("grpc", "http"):
            tls = self.protocols[proto]["tls"]
            for field in ("cert_file", "key_file", "client_ca_file"):
                value = str(tls[field])
                self.assertTrue(
                    value.startswith("${env:") or value.startswith("/"),
                    f"{proto}.{field} must be an env placeholder or mounted path, got {value!r}",
                )

    # spec: F38.ac13 — the production overlay is separate from the dev stack, mounts a
    # secret dir from the environment, and commits no PEM material.
    def test_production_compose_overlay_is_separate_and_secretless(self) -> None:
        with PROD_COMPOSE_PATH.open() as handle:
            compose = yaml.safe_load(handle)
        overlay_text = PROD_COMPOSE_PATH.read_text()
        for marker in PEM_MARKERS:
            self.assertNotIn(marker, overlay_text)
        service = compose["services"][COLLECTOR_SERVICE]
        mounts = " ".join(service["volumes"])
        # Loads the production TLS config, not the developer plaintext one.
        self.assertIn("collector-config.prod.yaml", mounts)
        self.assertNotIn("collector-config.yaml:/etc", mounts.replace("prod.yaml", "prod-yaml"))


def _docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    return subprocess.run(
        ("docker", "info"), capture_output=True, text=True
    ).returncode == 0


@unittest.skipUnless(_docker_available(), "Docker daemon required for CS-038 collector run")
class CollectorDockerTests(unittest.TestCase):
    """AC10–AC11: the pinned image validates the config and ingests an error trace."""

    def _run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(args, capture_output=True, text=True, check=check)

    # spec: F38.ac10 — pinned image accepts the committed config and rejects an invalid one.
    def test_validate_accepts_good_config_and_rejects_invalid(self) -> None:
        good = self._run(
            "docker", "run", "--rm", "--entrypoint", "/otelcol-contrib",
            "-v", f"{CONFIG_PATH}:/cfg.yaml:ro", PINNED_IMAGE,
            "validate", "--config", "/cfg.yaml", check=False,
        )
        self.assertEqual(0, good.returncode, f"good config rejected:\n{good.stderr}")

        with tempfile.TemporaryDirectory(prefix="cs038-bad-") as tempdir:
            bad = Path(tempdir) / "bad.yaml"
            # Unknown receiver + pipeline referencing it: must fail validation (fail-closed).
            bad.write_text(
                "receivers:\n  not_a_real_receiver: {}\n"
                "exporters:\n  debug: {}\n"
                "service:\n  pipelines:\n    traces:\n"
                "      receivers: [not_a_real_receiver]\n      exporters: [debug]\n"
            )
            rejected = self._run(
                "docker", "run", "--rm", "--entrypoint", "/otelcol-contrib",
                "-v", f"{bad}:/cfg.yaml:ro", PINNED_IMAGE,
                "validate", "--config", "/cfg.yaml", check=False,
            )
            self.assertNotEqual(0, rejected.returncode, "invalid config unexpectedly validated")

    # spec: F38.ac13 — the pinned image accepts the production TLS config (TLS paths
    # supplied via env) and rejects a deliberately incomplete TLS pair (cert, no key).
    def test_validate_accepts_prod_tls_config_and_rejects_incomplete(self) -> None:
        complete = self._run(
            "docker", "run", "--rm", "--entrypoint", "/otelcol-contrib",
            "-e", "OTEL_RECEIVER_TLS_CERT_FILE=/tls/server.crt",
            "-e", "OTEL_RECEIVER_TLS_KEY_FILE=/tls/server.key",
            "-e", "OTEL_RECEIVER_TLS_CLIENT_CA_FILE=/tls/client-ca.crt",
            "-v", f"{PROD_CONFIG_PATH}:/cfg.yaml:ro", PINNED_IMAGE,
            "validate", "--config", "/cfg.yaml", check=False,
        )
        self.assertEqual(0, complete.returncode, f"prod TLS config rejected:\n{complete.stderr}")

        incomplete = self._run(
            "docker", "run", "--rm", "--entrypoint", "/otelcol-contrib",
            "-e", "OTEL_RECEIVER_TLS_CERT_FILE=/tls/server.crt",
            "-e", "OTEL_RECEIVER_TLS_KEY_FILE=",  # key omitted → server TLS incomplete
            "-e", "OTEL_RECEIVER_TLS_CLIENT_CA_FILE=",
            "-v", f"{PROD_CONFIG_PATH}:/cfg.yaml:ro", PINNED_IMAGE,
            "validate", "--config", "/cfg.yaml", check=False,
        )
        self.assertNotEqual(0, incomplete.returncode, "incomplete server TLS unexpectedly validated")

    # spec: F38.ac13 — mTLS is genuinely optional: the pinned image validates the prod
    # config with server cert+key and NO OTEL_RECEIVER_TLS_CLIENT_CA_FILE provided.
    def test_prod_tls_validates_without_client_ca_so_mtls_is_optional(self) -> None:
        no_client_ca = self._run(
            "docker", "run", "--rm", "--entrypoint", "/otelcol-contrib",
            "-e", "OTEL_RECEIVER_TLS_CERT_FILE=/tls/server.crt",
            "-e", "OTEL_RECEIVER_TLS_KEY_FILE=/tls/server.key",
            # OTEL_RECEIVER_TLS_CLIENT_CA_FILE intentionally NOT passed → no mTLS.
            "-v", f"{PROD_CONFIG_PATH}:/cfg.yaml:ro", PINNED_IMAGE,
            "validate", "--config", "/cfg.yaml", check=False,
        )
        self.assertEqual(
            0, no_client_ca.returncode,
            f"prod TLS config without a client CA should validate (mTLS optional):\n{no_client_ca.stderr}",
        )

    # spec: F38.ac11 — an OTLP error trace is ingested and retained via the debug exporter.
    def test_isolated_collector_ingests_and_retains_error_trace(self) -> None:
        name = f"cs038-collector-{uuid.uuid4().hex[:10]}"
        trace_id = uuid.uuid4().hex  # 32 hex chars — a valid 16-byte OTLP trace id
        span_id = uuid.uuid4().hex[:16]  # 16 hex chars — a valid 8-byte OTLP span id
        try:
            started = self._run(
                "docker", "run", "-d", "--name", name,
                "-p", "127.0.0.1::4318", "-p", "127.0.0.1::13133",
                "--entrypoint", "/otelcol-contrib",
                "-v", f"{CONFIG_PATH}:/etc/otelcol-contrib/collector-config.yaml:ro",
                PINNED_IMAGE,
                "--config", "/etc/otelcol-contrib/collector-config.yaml", check=False,
            )
            self.assertEqual(0, started.returncode, f"collector did not start:\n{started.stderr}")

            http_port = self._host_port(name, "4318")
            health_port = self._host_port(name, "13133")
            self._wait_for_health(health_port)

            self._post_error_trace(http_port, trace_id, span_id)

            # tail_sampling buffers for decision_wait before exporting; wait past it, then read debug.
            logs = self._wait_for_log(name, trace_id, timeout=25)
            self.assertIn(trace_id, logs, "error trace was not retained by the debug exporter")
        finally:
            self._run("docker", "rm", "-f", name, check=False)

    def _host_port(self, name: str, container_port: str) -> str:
        mapping = self._run("docker", "port", name, container_port).stdout.strip()
        return mapping.splitlines()[0].rsplit(":", 1)[-1]

    def _wait_for_health(self, port: str, timeout: int = 30) -> None:
        deadline = time.time() + timeout
        last = ""
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2) as resp:
                    if resp.status == 200:
                        return
            except (urllib.error.URLError, ConnectionError, OSError) as exc:
                last = str(exc)
            time.sleep(1)
        self.fail(f"collector health endpoint 13133 never became ready: {last}")

    def _post_error_trace(self, port: str, trace_id: str, span_id: str) -> None:
        now_ns = time.time_ns()
        payload = {
            "resourceSpans": [{
                "resource": {"attributes": [
                    {"key": "service.name", "value": {"stringValue": "cs038-test"}}
                ]},
                "scopeSpans": [{
                    "scope": {"name": "cs038-test"},
                    "spans": [{
                        "traceId": trace_id,
                        "spanId": span_id,
                        "name": "cs038-error-span",
                        "kind": 1,
                        "startTimeUnixNano": str(now_ns),
                        "endTimeUnixNano": str(now_ns + 1_000_000),
                        "status": {"code": 2},  # STATUS_CODE_ERROR → tail sampling must keep it
                    }],
                }],
            }],
        }
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/traces",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as resp:
            self.assertIn(resp.status, (200, 202), "OTLP/HTTP ingest was not accepted")

    def _wait_for_log(self, name: str, needle: str, timeout: int) -> str:
        deadline = time.time() + timeout
        logs = ""
        while time.time() < deadline:
            logs = self._run("docker", "logs", name, check=False).stdout + \
                self._run("docker", "logs", name, check=False).stderr
            if needle in logs:
                return logs
            time.sleep(2)
        return logs


if __name__ == "__main__":
    unittest.main()
