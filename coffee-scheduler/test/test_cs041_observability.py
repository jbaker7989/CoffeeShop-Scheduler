"""CS-041 operation and call-site contracts, with no live transports."""
from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

import httpx
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
MANIFEST = ROOT / "scripts/ci/observability_boundaries.json"
PRIVATE = "synthetic-staff@example.test"
URL = "https://synthetic-peer.example.test/a2a"
TRACEPARENT = "00-00000000000000000000000000000042-0000000000000043-01"


class BoundaryTests(unittest.IsolatedAsyncioTestCase):
    verified = None

    # spec: F41.ac1 — Isolated exporter and context.
    # spec: F41.ac5 — Only in-process test transports are permitted.
    def setUp(self):
        self.provider_before = trace.get_tracer_provider()
        self.exporter = InMemorySpanExporter()
        provider = TracerProvider(shutdown_on_exit=False)
        provider.add_span_processor(SimpleSpanProcessor(self.exporter))
        self.addCleanup(provider.shutdown)
        self.tracer = provider.get_tracer("cs041-contract")
        fixtures = self.enterContext(ExitStack())
        fixtures.enter_context(patch.dict(os.environ))
        for key in tuple(os.environ):
            if key.startswith("OTEL_EXPORTER_OTLP") or key.startswith("DATABASE_URL"):
                os.environ.pop(key)
        for method in ("connect", "connect_ex", "sendto"):
            fixtures.enter_context(patch.object(socket.socket, method, side_effect=AssertionError("live socket")))
        fixtures.enter_context(patch.object(socket, "getaddrinfo", side_effect=AssertionError("live DNS")))
        fixtures.enter_context(patch("otel.boundary._get_tracer", return_value=self.tracer))
        self.registry = json.loads(MANIFEST.read_text())["boundaries"]
        self.assertEqual(self.exporter.get_finished_spans(), ())

    def tearDown(self):
        self.assertIs(trace.get_tracer_provider(), self.provider_before)

    def assert_boundary(self, key, *, operation, service, parent=None, remote=False, error=False):
        entry = self.registry[key]
        self.assertEqual(entry["test"], f"{type(self).__name__}.{self._testMethodName}")
        spans = [span for span in self.exporter.get_finished_spans() if span.name == entry["span"]]
        self.assertEqual(len(spans), 1, [span.name for span in self.exporter.get_finished_spans()])
        span = spans[0]
        self.assertEqual(span.kind.name, entry["kind"])
        self.assertIsNotNone(span.end_time)
        if parent is None:
            self.assertIsNone(span.parent)
        else:
            self.assertEqual(span.context.trace_id, parent.trace_id)
            self.assertEqual(span.parent.span_id, parent.span_id)
            self.assertEqual(span.parent.is_remote, remote)
        self.assertEqual(dict(span.attributes), {
            "operation": operation, "service": service, "outcome": "error" if error else "ok",
        })
        self.assertEqual(span.status.status_code, StatusCode.ERROR if error else StatusCode.UNSET)
        self.assertIsNone(span.status.description)
        self.assertEqual([(event.name, dict(event.attributes)) for event in span.events],
                         [("operation.error", {})] if error else [])
        if self.verified is not None:
            self.verified.add(key)
        return span.context

    # spec: F41.ac1 — Isolated exporter and context.
    def test_exporter_emit_read_reset(self):
        from opentelemetry.trace import SpanKind

        from otel.boundary import boundary_span

        with boundary_span("isolation.probe", SpanKind.INTERNAL, "test", "probe"):
            self.assertEqual(self.exporter.get_finished_spans(), ())
        self.assertEqual([span.name for span in self.exporter.get_finished_spans()], ["isolation.probe"])
        self.exporter.clear()
        self.assertEqual(self.exporter.get_finished_spans(), ())

    # spec: F41.ac2 — The operation has its own span.
    # spec: F41.ac3 — Error telemetry preserves caller behavior.
    # spec: F41.ac6 — Only bounded, safe attributes are exported.
    def test_http_client(self):
        from agents.a2a_transport import _post_stream

        for failure in (False, True):
            with self.subTest(failure=failure):
                self.exporter.clear()
                requests = []
                timeout = httpx.ReadTimeout(PRIVATE)

                def reply(request, requests=requests, failure=failure, timeout=timeout):
                    self.assertEqual(trace.get_current_span().name, "http.client.post")
                    requests.append(request)
                    if failure:
                        raise timeout
                    return httpx.Response(200, content=b"ok")

                with (
                    self.tracer.start_as_current_span("test-parent") as parent,
                    patch("agents.a2a_transport.httpx.Client",
                          side_effect=lambda **kw: httpx_client(transport=httpx.MockTransport(reply), **kw)),
                ):
                    if failure:
                        with self.assertRaises(httpx.ReadTimeout) as raised, _post_stream(
                            URL, {"private": PRIVATE}, {"Authorization": "Bearer " + PRIVATE}, 1
                        ):
                            self.fail("timeout must propagate")
                        self.assertIs(raised.exception, timeout)
                    else:
                        with _post_stream(URL, {"private": PRIVATE}, {"Authorization": "Bearer " + PRIVATE}, 1) as response:
                            self.assertEqual(response.read(), b"ok")
                self.assertEqual(len(requests), 1)
                self.assertEqual((requests[0].method, str(requests[0].url)), ("POST", URL))
                self.assert_boundary("http-client", operation="post", service="agent-peer",
                                     parent=parent.get_span_context(), error=failure)

    # spec: F41.ac2 — The operation has its own span.
    # spec: F41.ac3 — Error telemetry preserves caller behavior.
    # spec: F41.ac6 — Only bounded, safe attributes are exported.
    async def test_http_server(self):
        from api.app import create_app

        app = create_app(Mock(), fetch_stores=Mock())

        @app.get("/failure")
        async def fail():
            self.assertEqual(trace.get_current_span().name, "http.server.request")
            raise RuntimeError(PRIVATE)

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://gateway.example.test") as client:
            self.assertEqual((await client.get("/healthz", headers={"Traceparent": TRACEPARENT})).status_code, 200)
            remote = trace.SpanContext(0x42, 0x43, True, trace.TraceFlags(1))
            self.assert_boundary("http-server", operation="request", service="manager-gateway",
                                 parent=remote, remote=True)
            self.exporter.clear()
            with self.assertRaisesRegex(RuntimeError, PRIVATE):
                await client.get("/failure?email=" + PRIVATE, headers={"Traceparent": "malformed"})
            self.assert_boundary("http-server", operation="request", service="manager-gateway", error=True)

    # spec: F41.ac2 — The operation has its own span.
    # spec: F41.ac3 — Error telemetry preserves caller behavior.
    # spec: F41.ac6 — Only bounded, safe attributes are exported.
    async def test_a2a_roundtrip(self):
        from agents.a2a_transport import AgentClient, AgentError, create_agent_app

        async def handler(identity, data):
            self.assertEqual(trace.get_current_span().name, "a2a.message.receive")
            self.assertEqual(data["email"], PRIVATE)
            if data["fail"]:
                raise AgentError("controlled_failure")
            return {"result": PRIVATE}

        app = create_agent_app("scheduler", URL, Mock(), handler)

        def reply(request):
            async def send():
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as client:
                    response = await client.send(request)
                    return httpx.Response(response.status_code, headers=response.headers, content=response.content)
            return asyncio.run(send())

        transport = httpx.MockTransport(reply)
        for failure in (False, True):
            with self.subTest(failure=failure):
                self.exporter.clear()
                with (
                    self.tracer.start_as_current_span("test-parent") as parent,
                    patch("agents.a2a_transport.httpx.Client",
                          side_effect=lambda **kw: httpx_client(transport=transport, **kw)),
                ):
                    client = AgentClient(URL)
                    data = {"email": PRIVATE, "fail": failure}
                    if failure:
                        with self.assertRaisesRegex(AgentError, "^forecast_rejected$"):
                            await asyncio.to_thread(client.send, data, PRIVATE, timeout=1)
                    else:
                        self.assertEqual(await asyncio.to_thread(client.send, data, PRIVATE, timeout=1),
                                         {"result": PRIVATE})
                client_context = self.assert_boundary("a2a-client", operation="message/send", service="agent-peer",
                                                      parent=parent.get_span_context(), error=failure)
                self.assert_boundary("a2a-server", operation="message/send", service="scheduler",
                                     parent=client_context, remote=True, error=failure)
                http = next(span for span in self.exporter.get_finished_spans() if span.name == "http.client.post")
                self.assertEqual(http.parent.span_id, client_context.span_id)
                self.assertEqual(http.context.trace_id, client_context.trace_id)

    # spec: F41.ac1 — Isolated exporter and context.
    # spec: F41.ac3 — Error telemetry preserves caller behavior.
    async def test_a2a_context_isolation_and_cancellation(self):
        from agents.a2a_transport import create_agent_app

        active = []
        started = asyncio.Event()

        async def handler(identity, data):
            span_context = trace.get_current_span().get_span_context()
            active.append(span_context)
            if len(active) == 1:
                started.set()
                await asyncio.Future()
            return {"received": True}

        app = create_agent_app("scheduler", URL, Mock(), handler)
        body = {"jsonrpc": "2.0", "id": "synthetic", "method": "message/send", "params": {
            "message": {"kind": "message", "messageId": "synthetic", "role": "user",
                        "parts": [{"kind": "data", "data": {}}]},
        }}
        headers = {"Authorization": "Bearer " + PRIVATE}
        with self.tracer.start_as_current_span("ambient-parent") as ambient:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as client:
                pending = asyncio.create_task(client.post(URL, json=body, headers=headers))
                try:
                    await asyncio.wait_for(started.wait(), 2)
                    response = await client.post(URL, json=body, headers={**headers, "Traceparent": "malformed"})
                    self.assertEqual(response.json()["result"]["role"], "agent")
                finally:
                    pending.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await pending
            self.assertIs(trace.get_current_span(), ambient)
            spans = self.exporter.get_finished_spans()
        self.assertEqual(len(spans), 2)
        self.assertTrue(all(span.name == "a2a.message.receive" and span.parent is None for span in spans))
        self.assertEqual([span.status.status_code for span in spans], [StatusCode.UNSET, StatusCode.ERROR])
        self.assertEqual([span.context for span in spans], active[::-1])
        self.assertNotEqual(active[0].trace_id, active[1].trace_id)
        self.assertFalse(trace.get_current_span().get_span_context().is_valid)

    # spec: F41.ac2 — The operation has its own span.
    # spec: F41.ac3 — Error telemetry preserves caller behavior.
    # spec: F41.ac6 — Only bounded, safe attributes are exported.
    def test_database_execute(self):
        from data_access.session import execute_statement

        conn = Mock()
        sql = "SELECT private_email WHERE private_email = %s"
        error = RuntimeError(PRIVATE)
        outcomes = iter([conn.cursor.return_value, error])

        def execute(*_args):
            self.assertEqual(trace.get_current_span().name, "db.postgresql.execute")
            result = next(outcomes)
            if result is error:
                raise error
            return result

        conn.execute.side_effect = execute
        with self.tracer.start_as_current_span("test-parent") as parent:
            self.assertIs(execute_statement(conn, sql, (PRIVATE,)), conn.cursor.return_value)
            self.assert_boundary("db-execute", operation="execute", service="postgresql",
                                 parent=parent.get_span_context())
            self.exporter.clear()
            with self.assertRaises(RuntimeError) as raised:
                execute_statement(conn, sql, (PRIVATE,))
            self.assertIs(raised.exception, error)
            self.assert_boundary("db-execute", operation="execute", service="postgresql",
                                 parent=parent.get_span_context(), error=True)
        self.assertEqual(conn.execute.call_count, 2)
        conn.execute.assert_called_with(sql, (PRIVATE,))

    # spec: F41.ac2 — The operation has its own span.
    # spec: F41.ac3 — Error telemetry preserves caller behavior.
    # spec: F41.ac6 — Only bounded, safe attributes are exported.
    def test_scheduler_tool(self):
        from agents.scheduler.workflow import ScheduleWorkflow

        error = RuntimeError(PRIVATE)
        outcomes = iter([PRIVATE, error])

        def execute(_state):
            self.assertEqual(trace.get_current_span().name, "tool.scheduler.invoke")
            result = next(outcomes)
            if result is error:
                raise error
            return result

        tool_implementation = Mock(side_effect=execute)
        node = ScheduleWorkflow._node("gather", tool_implementation)
        state = {"pins": {"synthetic_private": PRIVATE}}
        with self.tracer.start_as_current_span("test-parent") as parent:
            self.assertEqual(node(state), PRIVATE)
            self.assert_boundary("tool-dispatch", operation="invoke", service="scheduler",
                                 parent=parent.get_span_context())
            self.exporter.clear()
            with self.assertRaises(RuntimeError) as raised:
                node(state)
            self.assertIs(raised.exception, error)
            self.assert_boundary("tool-dispatch", operation="invoke", service="scheduler",
                                 parent=parent.get_span_context(), error=True)
        self.assertEqual(tool_implementation.call_count, 2)
        tool_implementation.assert_called_with(state)

    # spec: F41.ac3 — Error telemetry preserves caller behavior.
    def test_telemetry_failure_preserves_business_result_and_exception(self):
        from data_access.session import execute_statement

        conn = Mock()
        with patch("otel.boundary._get_tracer", side_effect=RuntimeError(PRIVATE)):
            self.assertIs(execute_statement(conn, "SELECT 1"), conn.execute.return_value)
        with (
            patch.object(self.exporter, "export", side_effect=RuntimeError(PRIVATE)),
            self.assertLogs("opentelemetry.sdk.trace.export", level="ERROR"),
        ):
            self.assertIs(execute_statement(conn, "SELECT 1"), conn.execute.return_value)
            error = ValueError(PRIVATE)
            conn.execute.side_effect = error
            with self.assertRaises(ValueError) as raised:
                execute_statement(conn, "SELECT 1")
            self.assertIs(raised.exception, error)
        self.assertFalse(trace.get_current_span().get_span_context().is_valid)


httpx_client = httpx.Client


class CallSiteTests(unittest.TestCase):
    # spec: F41.ac4 — Unregistered call sites fail.
    # spec: F41.ac7 — Dedicated CI exercises the contract and scanner.
    def test_uninstrumented_wrapper_fails_and_registered_adapter_passes(self):
        from scripts.ci.check_observability import check

        path = "agents/new_wrapper.py"
        with tempfile.TemporaryDirectory() as tmp:
            wrapper = Path(tmp) / path
            wrapper.parent.mkdir()
            wrapper.write_text("def wrapper(conn, sql):\n    run = conn.execute\n    return run(sql)\n")
            rejected = check(ROOT, extra_sources={path: wrapper.read_text()})
            self.assertTrue(rejected)
            self.assertTrue(all(item.startswith(f"{path}:") and "unregistered" in item for item in rejected), rejected)
            self.assertTrue(any(f"{path}:3:" in item for item in rejected), rejected)
            wrapper.write_text("from data_access.session import execute_statement\n"
                               "def wrapper(conn, sql):\n    return execute_statement(conn, sql)\n")
            self.assertEqual(check(ROOT, extra_sources={path: wrapper.read_text()}), [])

    # spec: F41.ac4 — Unregistered call sites fail.
    def test_call_shapes_are_checked(self):
        from scripts.ci.check_observability import inspect_source

        sources = {
            "HTTP import alias": "from urllib.request import urlopen as open_url\nfetch = open_url\nfetch('fake')",
            "HTTP constructor alias and multiline call": "from httpx import Client as C\nC().get(\n 'fake')",
            "HTTP method alias": "import httpx as h\ncall = h.get\ncall('fake')",
            "DB method alias": "def wrapper(conn):\n    run = conn.execute\n    return run(\n        'SELECT 1')",
            "explicit bound call": "def wrapper(conn):\n    return conn.execute.__call__('SELECT 1')",
            "tool helper": "def helper(tool):\n    return tool.invoke({})",
            "dynamic method": "def helper(conn, method):\n    return getattr(conn, method)('SELECT 1')",
            "opaque callable": "def helper(conn):\n    return getattr(conn, 'unknown_method')()",
            "reflected SDK": "import httpx as h\nvars(h)['get']('fake')",
            "chained reflection": "def wrapper(client):\n    return client.__dict__.get('send')()",
            "reflected tool": "from operator import methodcaller as call\ncall('invoke', {})(tool)",
            "helper escape": "import httpx as h\ndef helper():\n    return h\nhelper().get('fake')",
            "injected HTTP operation": "def helper(c):\n    return c.get('https://example.test')",
        }
        for name, source in sources.items():
            with self.subTest(name=name):
                self.assertTrue(inspect_source("new_wrapper.py", source))
        self.assertFalse(inspect_source("new_wrapper.py", "def wrapper(data):\n    return data.get('name')"))
        for call in ("C().get(url)", "with C() as c:\n        c.get(url)"):
            source = "from httpx import Client as C\ndef wrapper(url):\n    " + call
            original = {site.key for site in inspect_source("new_wrapper.py", source)}
            changed = {site.key for site in inspect_source("new_wrapper.py", source.replace(".get(url)", ".get(str(url))"))}
            self.assertTrue(changed - original, "an unchanged client constructor cannot exempt a changed request")

    # spec: F41.ac4 — Unregistered call sites fail.
    # spec: F41.ac7 — Dedicated CI exercises the contract and scanner.
    def test_unmatched_boundary_test_and_changed_exemption_fail(self):
        from scripts.ci.check_observability import check

        manifest = json.loads(MANIFEST.read_text())
        manifest["boundaries"]["db-execute"]["test"] = "BoundaryTests.test_missing"
        self.assertTrue(any("test_missing" in item for item in check(ROOT, manifest=manifest)))
        path = "otel/demo/service_a.py"
        original = (ROOT / path).read_text()
        self.assertTrue(any("unregistered" in item for item in check(
            ROOT, extra_sources={path: original.replace("urlopen(request, timeout=5)", "urlopen(request, timeout=6)")}
        )))

    # spec: F41.ac7 — Dedicated CI exercises the contract and scanner.
    def test_workflow_has_dedicated_contract_and_gate(self):
        workflow = (ROOT.parent / ".github/workflows/ci.yml").read_text()
        job = workflow.split("\n  observability-contract:", 1)[1].split("\n  lint:", 1)[0]
        self.assertIn("sudo unshare --net", job)
        self.assertIn("-m unittest discover -s test -p 'test_cs041_observability.py'", job)
        self.assertIn("python scripts/ci/check_observability.py", job)


def load_tests(loader, suite, _pattern):
    # Record proof from the actual running tests. A name in the manifest or a
    # dormant assertion in a test method cannot satisfy the contract.
    verified = set()
    for group in suite:
        for case in group:
            if isinstance(case, BoundaryTests):
                case.verified = verified

    # spec: F41.ac2 — The operation has its own span.
    # spec: F41.ac7 — Dedicated CI exercises the contract and scanner.
    def all_registered_boundaries_were_exercised():
        expected = set(json.loads(MANIFEST.read_text())["boundaries"])
        unittest.TestCase().assertEqual(verified, expected)

    suite.addTest(unittest.FunctionTestCase(all_registered_boundaries_were_exercised))
    return suite


if __name__ == "__main__":
    unittest.main()
