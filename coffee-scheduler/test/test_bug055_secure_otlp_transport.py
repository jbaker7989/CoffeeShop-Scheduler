"""BUG-055 / F36 — secure OTLP transport classification and TLS credentials.

Run with:
    python3 -m unittest coffee-scheduler/test/test_bug055_secure_otlp_transport.py

These tests extend the F36 bootstrap contract with the BUG-055 security model:
plaintext gRPC export is permitted only for an explicitly recognized loopback
endpoint; any non-loopback target must present TLS material (a readable CA bundle,
plus a complete client certificate/key pair for mTLS) *before* either exporter is
constructed. Every case patches ``OTLPSpanExporter``/``OTLPMetricExporter`` (and gRPC
credential construction) so a rejection is proven to happen with no exporter built and
no DNS/network I/O. The telemetry module is loaded fresh per test so its process-global
provider state and ``_initialized`` identity reset between cases.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
TELEMETRY_PATH = ROOT / "coffee-scheduler" / "otel" / "telemetry.py"

ENV_ENDPOINT = "OTEL_EXPORTER_OTLP_ENDPOINT"
ENV_CA = "OTEL_EXPORTER_OTLP_CERTIFICATE"
ENV_CLIENT_CERT = "OTEL_EXPORTER_OTLP_CLIENT_CERTIFICATE"
ENV_CLIENT_KEY = "OTEL_EXPORTER_OTLP_CLIENT_KEY"
_TLS_ENV = (ENV_ENDPOINT, ENV_CA, ENV_CLIENT_CERT, ENV_CLIENT_KEY)


def _pem(label: bytes, payload: bytes) -> bytes:
    """A structurally valid PEM block (BEGIN/END + base64 body) for a synthetic input.

    These are not real keys/certs — the validator checks PEM framing + base64, not
    cryptographic validity — so no secret material is committed in the fixtures.
    """
    import base64 as _b64

    body = _b64.b64encode(payload)
    return b"-----BEGIN " + label + b"-----\n" + body + b"\n-----END " + label + b"-----\n"


VALID_CA = _pem(b"CERTIFICATE", b"synthetic-ca-der")
VALID_CLIENT_CERT = _pem(b"CERTIFICATE", b"synthetic-client-cert-der")
VALID_CLIENT_KEY = _pem(b"PRIVATE KEY", b"synthetic-client-key-der")
# Readable but structurally invalid material (not PEM at all).
MALFORMED_PEM = b"this is not a pem file\n"


def _load_telemetry():
    """Import telemetry.py as a throwaway module so globals reset per test."""
    spec = importlib.util.spec_from_file_location("bug055_telemetry", TELEMETRY_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _TransportTestCase(unittest.TestCase):
    """Shared harness: a clean TLS env and patched exporter/credential constructors."""

    def setUp(self) -> None:
        self.telemetry = _load_telemetry()
        # Start from a known-clean transport env; each test sets what it needs.
        self._env_patch = mock.patch.dict(os.environ, {}, clear=False)
        self._env_patch.start()
        for key in _TLS_ENV:
            os.environ.pop(key, None)

        self.span_ctor = mock.patch.object(self.telemetry, "OTLPSpanExporter").start()
        self.metric_ctor = mock.patch.object(self.telemetry, "OTLPMetricExporter").start()
        # A sentinel credentials object so we can assert identity without real gRPC TLS.
        # Hold the patcher (not the mock) so a test can stop it to exercise the real
        # credential builder + PEM reads.
        self.sentinel_creds = object()
        self._creds_patcher = mock.patch.object(
            self.telemetry, "_build_channel_credentials", return_value=self.sentinel_creds
        )
        self.creds_builder = self._creds_patcher.start()
        self.addCleanup(mock.patch.stopall)
        self.addCleanup(self._env_patch.stop)

    def _use_real_credential_builder(self) -> None:
        """Stop the credential-builder patch so a test exercises the real PEM reads."""
        self._creds_patcher.stop()

    def _init(self, service: str = "scheduler", version: str = "0.1.0"):
        return self.telemetry.init_telemetry(service, version)

    def _assert_no_exporters_built(self) -> None:
        self.span_ctor.assert_not_called()
        self.metric_ctor.assert_not_called()


class LoopbackPlaintextTests(_TransportTestCase):
    """spec: F36.ac18 — plaintext is allowed only for explicit loopback targets."""

    def _assert_plaintext(self) -> None:
        self._init()
        for ctor in (self.span_ctor, self.metric_ctor):
            self.assertEqual(1, ctor.call_count)
            kwargs = ctor.call_args.kwargs
            self.assertIs(True, kwargs.get("insecure"))
            self.assertIsNone(kwargs.get("credentials"))
        self.creds_builder.assert_not_called()

    # spec: F36.ac18 — the default (unset endpoint) stays plaintext localhost.
    def test_default_endpoint_is_plaintext_local(self) -> None:
        self._assert_plaintext()

    # spec: F36.ac18 — IPv4 loopback is plaintext.
    def test_ipv4_loopback_is_plaintext(self) -> None:
        os.environ[ENV_ENDPOINT] = "127.0.0.1:4317"
        self._assert_plaintext()

    # spec: F36.ac18 — the whole 127.0.0.0/8 range is loopback.
    def test_ipv4_loopback_range_is_plaintext(self) -> None:
        os.environ[ENV_ENDPOINT] = "127.5.6.7:4317"
        self._assert_plaintext()

    # spec: F36.ac18 — bracketed IPv6 loopback is accepted and plaintext.
    def test_bracketed_ipv6_loopback_is_plaintext(self) -> None:
        os.environ[ENV_ENDPOINT] = "[::1]:4317"
        self._assert_plaintext()

    # spec: F36.ac18 — localhost classification is explicitly case-insensitive.
    def test_localhost_is_case_insensitive(self) -> None:
        os.environ[ENV_ENDPOINT] = "LOCALHOST:4317"
        self._assert_plaintext()

    # spec: F36.ac18 — a name that merely *resolves* to loopback is still remote
    # (no DNS trust decision), so without TLS it fails closed rather than going plaintext.
    def test_hostname_resolving_to_loopback_is_remote(self) -> None:
        os.environ[ENV_ENDPOINT] = "localhost.localdomain.example:4317"
        with self.assertRaises(self.telemetry.TelemetryConfigError):
            self._init()
        self._assert_no_exporters_built()


class RemoteTlsTests(_TransportTestCase):
    """spec: F36.ac19 — non-loopback endpoints require a readable CA bundle."""

    def setUp(self) -> None:
        super().setUp()
        self._tmp = Path(self.enterContext(self._tempdir()))

    def _tempdir(self):
        import tempfile

        return tempfile.TemporaryDirectory(prefix="bug055-tls-")

    def _write(self, name: str, data: bytes) -> str:
        path = self._tmp / name
        path.write_bytes(data)
        return str(path)

    # spec: F36.ac19 — a remote IPv4 target with a readable CA builds secure exporters.
    def test_remote_ip_with_ca_builds_secure_exporters(self) -> None:
        os.environ[ENV_ENDPOINT] = "10.0.0.5:4317"
        os.environ[ENV_CA] = self._write("ca.pem", b"-----CA-----\n")
        self._init()
        for ctor in (self.span_ctor, self.metric_ctor):
            kwargs = ctor.call_args.kwargs
            self.assertIs(False, kwargs.get("insecure"))
            self.assertIs(self.sentinel_creds, kwargs.get("credentials"))

    # spec: F36.ac19 — a remote hostname with no CA fails closed before any exporter.
    def test_remote_without_ca_rejected(self) -> None:
        os.environ[ENV_ENDPOINT] = "collector.example.com:4317"
        with self.assertRaises(self.telemetry.TelemetryConfigError):
            self._init()
        self._assert_no_exporters_built()

    # spec: F36.ac19 — an unreadable CA raises a descriptive, secret-free error.
    def test_remote_unreadable_ca_rejected(self) -> None:
        # Real credential builder (not the sentinel) so the file read is exercised.
        self._use_real_credential_builder()
        os.environ[ENV_ENDPOINT] = "collector.example.com:4317"
        missing = str(self._tmp / "does-not-exist.pem")
        os.environ[ENV_CA] = missing
        with self.assertRaises(self.telemetry.TelemetryConfigError) as ctx:
            self._init()
        self._assert_no_exporters_built()
        # The message must not leak the secret path or any PEM content.
        self.assertNotIn(missing, str(ctx.exception))

    # spec: F36.ac19 — an empty CA bundle is rejected.
    def test_remote_empty_ca_rejected(self) -> None:
        self._use_real_credential_builder()
        os.environ[ENV_ENDPOINT] = "collector.example.com:4317"
        os.environ[ENV_CA] = self._write("empty.pem", b"   \n")
        with self.assertRaises(self.telemetry.TelemetryConfigError):
            self._init()
        self._assert_no_exporters_built()

    # spec: F36.ac19 — a readable but MALFORMED (non-PEM) CA fails closed before any
    # exporter, and the error reveals neither the file path nor the raw bytes.
    def test_remote_malformed_ca_rejected(self) -> None:
        self._use_real_credential_builder()  # real _read_pem + _validate_pem
        os.environ[ENV_ENDPOINT] = "collector.example.com:4317"
        ca_path = self._write("ca.pem", MALFORMED_PEM)
        os.environ[ENV_CA] = ca_path
        with mock.patch.object(self.telemetry.grpc, "ssl_channel_credentials") as ssl:
            with self.assertRaises(self.telemetry.TelemetryConfigError) as ctx:
                self._init()
        ssl.assert_not_called()  # never reached gRPC credential construction
        self._assert_no_exporters_built()
        message = str(ctx.exception)
        self.assertNotIn(ca_path, message)
        self.assertNotIn(MALFORMED_PEM.decode(), message)

    # spec: F36.ac19 — a valid PEM CA whose block is not a CERTIFICATE is rejected.
    def test_remote_ca_with_wrong_block_type_rejected(self) -> None:
        self._use_real_credential_builder()
        os.environ[ENV_ENDPOINT] = "collector.example.com:4317"
        os.environ[ENV_CA] = self._write("ca.pem", VALID_CLIENT_KEY)  # a key, not a cert
        with mock.patch.object(self.telemetry.grpc, "ssl_channel_credentials") as ssl:
            with self.assertRaises(self.telemetry.TelemetryConfigError):
                self._init()
        ssl.assert_not_called()
        self._assert_no_exporters_built()


class MutualTlsTests(_TransportTestCase):
    """spec: F36.ac20 — the client cert/key pair is all-or-nothing."""

    def setUp(self) -> None:
        super().setUp()
        import tempfile

        self._tmp = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="bug055-mtls-")))
        os.environ[ENV_ENDPOINT] = "collector.example.com:4317"
        os.environ[ENV_CA] = self._write("ca.pem", VALID_CA)

    def _write(self, name: str, data: bytes) -> str:
        path = self._tmp / name
        path.write_bytes(data)
        return str(path)

    # spec: F36.ac20 — a client cert without its key is rejected before construction.
    def test_client_cert_without_key_rejected(self) -> None:
        os.environ[ENV_CLIENT_CERT] = self._write("client.crt", b"-----CERT-----\n")
        with self.assertRaises(self.telemetry.TelemetryConfigError):
            self._init()
        self._assert_no_exporters_built()

    # spec: F36.ac20 — a client key without its cert is rejected before construction.
    def test_client_key_without_cert_rejected(self) -> None:
        os.environ[ENV_CLIENT_KEY] = self._write("client.key", b"-----KEY-----\n")
        with self.assertRaises(self.telemetry.TelemetryConfigError):
            self._init()
        self._assert_no_exporters_built()

    # spec: F36.ac20 — a complete CA+cert+key triple feeds all three PEMs to gRPC.
    def test_full_mtls_passes_client_material_to_grpc(self) -> None:
        self._use_real_credential_builder()  # exercise real builder + patched grpc
        os.environ[ENV_CLIENT_CERT] = self._write("client.crt", VALID_CLIENT_CERT)
        os.environ[ENV_CLIENT_KEY] = self._write("client.key", VALID_CLIENT_KEY)
        with mock.patch.object(self.telemetry.grpc, "ssl_channel_credentials") as ssl:
            ssl.return_value = object()
            self._init()
        ssl.assert_called_once()
        kwargs = ssl.call_args.kwargs
        self.assertEqual(VALID_CA, kwargs.get("root_certificates"))
        self.assertEqual(VALID_CLIENT_KEY, kwargs.get("private_key"))
        self.assertEqual(VALID_CLIENT_CERT, kwargs.get("certificate_chain"))

    # spec: F36.ac20 — a malformed (non-PEM) client certificate fails closed before gRPC.
    def test_malformed_client_cert_rejected(self) -> None:
        self._use_real_credential_builder()
        cert_path = self._write("client.crt", MALFORMED_PEM)
        os.environ[ENV_CLIENT_CERT] = cert_path
        os.environ[ENV_CLIENT_KEY] = self._write("client.key", VALID_CLIENT_KEY)
        with mock.patch.object(self.telemetry.grpc, "ssl_channel_credentials") as ssl:
            with self.assertRaises(self.telemetry.TelemetryConfigError) as ctx:
                self._init()
        ssl.assert_not_called()
        self._assert_no_exporters_built()
        self.assertNotIn(cert_path, str(ctx.exception))
        self.assertNotIn(MALFORMED_PEM.decode(), str(ctx.exception))

    # spec: F36.ac20 — a malformed (non-PEM) client key fails closed before gRPC.
    def test_malformed_client_key_rejected(self) -> None:
        self._use_real_credential_builder()
        os.environ[ENV_CLIENT_CERT] = self._write("client.crt", VALID_CLIENT_CERT)
        key_path = self._write("client.key", MALFORMED_PEM)
        os.environ[ENV_CLIENT_KEY] = key_path
        with mock.patch.object(self.telemetry.grpc, "ssl_channel_credentials") as ssl:
            with self.assertRaises(self.telemetry.TelemetryConfigError) as ctx:
                self._init()
        ssl.assert_not_called()
        self._assert_no_exporters_built()
        self.assertNotIn(key_path, str(ctx.exception))

    # spec: F36.ac20 — a client key whose PEM block is a CERTIFICATE (wrong type) is rejected.
    def test_client_key_with_certificate_block_rejected(self) -> None:
        self._use_real_credential_builder()
        os.environ[ENV_CLIENT_CERT] = self._write("client.crt", VALID_CLIENT_CERT)
        os.environ[ENV_CLIENT_KEY] = self._write("client.key", VALID_CLIENT_CERT)  # cert, not key
        with mock.patch.object(self.telemetry.grpc, "ssl_channel_credentials") as ssl:
            with self.assertRaises(self.telemetry.TelemetryConfigError):
                self._init()
        ssl.assert_not_called()
        self._assert_no_exporters_built()


class MalformedEndpointTests(_TransportTestCase):
    """spec: F36.ac21 — bad endpoints fail closed before any exporter/DNS/network I/O."""

    BAD = {
        "scheme": "http://collector.example.com:4317",
        "userinfo": "user@collector.example.com:4317",
        "path": "collector.example.com:4317/v1/traces",
        "query": "collector.example.com:4317?tls=off",
        "missing_port": "collector.example.com",
        "zero_port": "collector.example.com:0",
        "high_port": "collector.example.com:70000",
        "unbracketed_ipv6": "::1:4317",
        "leading_whitespace": " localhost:4317",
        "trailing_whitespace": "localhost:4317 ",
        "empty": "",
    }

    # spec: F36.ac21 — every malformed endpoint raises and constructs no exporter.
    def test_malformed_endpoints_are_rejected(self) -> None:
        for label, value in self.BAD.items():
            with self.subTest(endpoint=label):
                self.span_ctor.reset_mock()
                self.metric_ctor.reset_mock()
                os.environ[ENV_ENDPOINT] = value
                with self.assertRaises(self.telemetry.TelemetryConfigError):
                    self._init()
                self._assert_no_exporters_built()


class TransportIdentityTests(_TransportTestCase):
    """spec: F36.ac22 — transport settings are part of initialization identity."""

    # spec: F36.ac22 — changing the endpoint/TLS mode after init fails closed.
    def test_changed_transport_after_init_rejected(self) -> None:
        import tempfile

        tmp = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="bug055-id-")))
        os.environ[ENV_ENDPOINT] = "localhost:4317"
        self._init("scheduler", "1.2.3")
        # Same service + version, but now a remote (TLS) trust boundary.
        os.environ[ENV_ENDPOINT] = "collector.example.com:4317"
        (tmp / "ca.pem").write_bytes(b"-----CA-----\n")
        os.environ[ENV_CA] = str(tmp / "ca.pem")
        with self.assertRaises(RuntimeError):
            self._init("scheduler", "1.2.3")

    # spec: F36.ac22 — an identical transport still reuses the first (tracer, meter).
    def test_identical_transport_is_idempotent(self) -> None:
        os.environ[ENV_ENDPOINT] = "localhost:4317"
        first = self._init("scheduler", "1.2.3")
        second = self._init("scheduler", "1.2.3")
        self.assertIs(first[0], second[0])
        self.assertIs(first[1], second[1])


if __name__ == "__main__":
    unittest.main()
