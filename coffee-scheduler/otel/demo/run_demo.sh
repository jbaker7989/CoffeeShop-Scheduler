#!/bin/sh
# Two-service A2A trace proof for F36 / CS-036.
#
# Runs service_b (stdlib http.server callee) then service_a (urllib client), both
# exporting over OTLP/gRPC to a *already-running* Collector (CS-038) at
# ${OTEL_EXPORTER_OTLP_ENDPOINT:-localhost:4317}. It then asserts, from the
# Collector's `debug` exporter output, that the CLIENT and SERVER spans share one
# trace_id (AC16) and that an A2A RED metric landed (AC17). Exit 0 only when both
# hold. This script expects the Collector to be up already; it does not start or
# stop the shared Compose stack.
#
# Usage (from a host with Docker + the Collector running):
#   docker compose -f docker-compose.observability.yml up -d
#   sh coffee-scheduler/otel/demo/run_demo.sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
OTEL_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
REPO_ROOT=$(CDPATH= cd -- "$OTEL_DIR/../.." && pwd)
COMPOSE_FILE="$REPO_ROOT/docker-compose.observability.yml"
COLLECTOR_SERVICE="otel-collector"

export OTEL_EXPORTER_OTLP_ENDPOINT="${OTEL_EXPORTER_OTLP_ENDPOINT:-localhost:4317}"
export OTEL_PROPAGATORS="${OTEL_PROPAGATORS:-tracecontext,baggage}"

# --- Resolve a Python with the OTel SDK installed (prefer a local venv) ---------
VENV="$OTEL_DIR/.venv"
if [ -x "$VENV/bin/python" ]; then
  PY="$VENV/bin/python"
else
  python3 -m venv "$VENV"
  PY="$VENV/bin/python"
  "$PY" -m pip install --quiet --upgrade pip
  "$PY" -m pip install --quiet -r "$OTEL_DIR/requirements.txt"
fi

# service_a / service_b import `telemetry` by name, so run them from otel/.
cd "$OTEL_DIR"

LOG_DIR=$(mktemp -d)
B_LOG="$LOG_DIR/service_b.log"
A_LOG="$LOG_DIR/service_a.log"
cleanup() {
  [ -n "${B_PID:-}" ] && kill "$B_PID" 2>/dev/null || true
  rm -rf "$LOG_DIR"
}
trap cleanup EXIT INT TERM

# --- Start the callee, wait for it to listen -----------------------------------
"$PY" demo/service_b.py >"$B_LOG" 2>&1 &
B_PID=$!
i=0
while [ "$i" -lt 50 ]; do
  grep -q "service_b listening" "$B_LOG" 2>/dev/null && break
  i=$((i + 1))
  sleep 0.1
done

# --- Run the caller; its own exit code is the local trace-equality check --------
set +e
"$PY" demo/service_a.py >"$A_LOG" 2>&1
A_RC=$?
set -e
cat "$A_LOG"

CLIENT_TID=$(sed -n 's/^CLIENT trace_id=//p' "$A_LOG" | head -n1)
if [ "$A_RC" -ne 0 ] || [ -z "$CLIENT_TID" ]; then
  echo "FAIL: service_a reported no single CLIENT/SERVER trace_id (rc=$A_RC)" >&2
  cat "$B_LOG" >&2 || true
  exit 1
fi

# --- Verify against the Collector `debug` exporter output ------------------------
# Give the BatchSpanProcessor / tail-sampler decision_wait time to flush+emit.
sleep 7
if ! command -v docker >/dev/null 2>&1; then
  echo "FAIL: docker not available to read Collector debug output" >&2
  exit 1
fi
DEBUG=$(docker compose -f "$COMPOSE_FILE" logs --no-color --no-log-prefix "$COLLECTOR_SERVICE" 2>/dev/null \
  || docker logs coffeeshop-otel-collector 2>&1)

# AC16: the one trace_id appears in the Collector's span debug output.
if ! printf '%s\n' "$DEBUG" | grep -q "$CLIENT_TID"; then
  echo "FAIL: trace_id $CLIENT_TID not found in Collector debug output (AC16)" >&2
  exit 1
fi

# AC17: at least one A2A RED metric landed in the Collector debug metrics.
METRIC=$(printf '%s\n' "$DEBUG" | grep -Eo 'a2a\.(calls|server\.duration_ms)' | head -n1)
if [ -z "$METRIC" ]; then
  echo "FAIL: no a2a.calls / a2a.server.duration_ms in Collector debug metrics (AC17)" >&2
  exit 1
fi

echo "PASS: single trace_id=$CLIENT_TID verified in Collector debug output (AC16)"
echo "PASS: A2A RED metric $METRIC observed in Collector debug metrics (AC17)"
exit 0
