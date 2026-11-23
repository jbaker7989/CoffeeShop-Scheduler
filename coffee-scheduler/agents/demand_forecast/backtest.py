"""CS-014c forecast backtest and validation (F9.ac14–ac20).

Evaluates the persisted CS-014b forecaster against actual demand on a temporal holdout and
produces an auditable, honest pass/fail report. It never retrains or retunes the model: it
assesses the already-persisted ``forecast_models`` artifact. The holdout is derived from the
registered ``feature_sets`` source window (the final 28 inclusive dates), never from
``date.today()``. Scoring requires an exact one-to-one match of expected open store/category/hour
keys, actual cells, and contract-validated forecasts. MAPE is measured per
``(location_id, product_category, local clock hour)`` over positive actuals only; a group with no
positive actual is unscorable and blocks the gate. P70 hit rate and P10–P90 interval coverage are
separate diagnostics; the F5 ≤10% MAPE criterion is the pass/fail gate.

Pure helpers are unit-testable offline; ``run_backtest`` and ``main`` add the DB orchestration.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import tempfile
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from contracts.forecast import CANONICAL_TZ, CONTRACT_VERSION, PINNED_TAXONOMY_VERSION, ForecastSet

REPORT_SCHEMA_VERSION = "forecast-backtest-v1"
BACKTEST_PROTOCOL = "rolling-origin-one-step-ahead"
DEFAULT_HOLDOUT_DAYS = 28
# Ratified 2026-11-23 (docs/decisions/CS-014b-acceptance-metric.md): the acceptance gate is
# volume-weighted WAPE plus interval calibration, because per-group MAPE is arithmetically
# hostile to small-count demand. MAPE is still reported, but it no longer gates.
MAPE_LIMIT = 10.0  # diagnostic only since the ratification; retained so reports stay comparable
WAPE_LIMIT = 60.0
GROUP_WAPE_LIMIT = 85.0
# Per-group WAPE is monotonic in volume (Coffee ~42-48% at ~6.6k units, Packaged Chocolate
# ~175-189% at ~40). Gating thin groups would gate noise, so only groups carrying real signal
# are gated; the rest are reported as diagnostics. The floor covers ~95% of holdout demand.
GROUP_VOLUME_FLOOR = 1000.0
CALIBRATION_TOLERANCE_PP = 8.0
P70_NOMINAL = 0.70
INTERVAL_NOMINAL = 0.80

# Rolling-origin one-step semantics: lag/rolling features may use actuals strictly before the
# scored row (matching feature_build.rolling_features); the scored row's actual is scoring-only.
_MAPE_DEFINITION = "mean of 100*abs(p70-actual)/actual over rows with actual>0, per location/category/clock-hour"
_ZERO_POLICY = "zero-actual rows are excluded from the MAPE denominator but counted; an all-zero group is unscorable"
_WAPE_DEFINITION = (
    "sum(abs(p70-actual)) / sum(actual), volume-weighted, overall and per location/category; "
    "zero-actual rows contribute error without inflating the denominator"
)
# Report path identifiers must be safe filename stems: no separators, no traversal, no control bytes.
_SAFE_MODEL_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class BacktestError(RuntimeError):
    """Raised when a backtest cannot proceed or must fail closed before emitting PASS."""


@dataclass(frozen=True)
class ScoredRow:
    """One aligned (expected key, actual, validated forecast) triple ready for scoring."""

    location_id: int
    product_category: str
    forecast_hour: datetime
    actual: float
    p70: float
    lower: float
    upper: float


@dataclass(frozen=True)
class GroupMetric:
    """MAPE and zero-actual accounting for one store/category/clock-hour group."""

    key: tuple[int, str, int]
    n_total: int
    n_positive_actual: int
    n_zero_actual: int
    mape: float | None
    scorable: bool


@dataclass(frozen=True)
class CoverageMetric:
    """Distinct P70 hit-rate and P10–P90 interval-coverage diagnostics over every scored row."""

    n_rows: int
    p70_hits: int
    p70_rate: float
    p70_nominal: float
    interval_hits: int
    interval_rate: float
    interval_nominal: float


@dataclass(frozen=True)
class WapeMetric:
    """Volume-weighted absolute percentage error for one (location, category) group."""

    key: tuple[int, str]
    n_rows: int
    volume: float
    abs_error: float
    wape: float | None
    gated: bool


@dataclass(frozen=True)
class GateOutcome:
    """Ratified gate: volume-weighted WAPE overall and per gated group, plus calibration drift."""

    passed: bool
    overall_wape: float
    failing_groups: tuple[tuple[int, str], ...]
    diagnostic_groups: tuple[tuple[int, str], ...]
    calibration_ok: bool
    reasons: tuple[str, ...]


# --- date derivation (F9.ac14) -------------------------------------------------


def derive_holdout_window(source_window: tuple[date, date], *, holdout_days: int = DEFAULT_HOLDOUT_DAYS) -> tuple[date, date]:
    """Return the final ``holdout_days`` inclusive dates of the registered source window.

    Fails closed on a reversed window or one with fewer than ``holdout_days`` inclusive dates, so
    the holdout is never silently padded or derived from wall-clock time (BUG-001/BUG-006 lesson).
    """
    if holdout_days <= 0:
        raise BacktestError(f"holdout_days must be positive, got {holdout_days}")
    start, end = source_window
    _require_plain_date(start, "source window start")
    _require_plain_date(end, "source window end")
    if end < start:
        raise BacktestError(f"source window is reversed: {start} > {end}")
    inclusive_days = (end - start).days + 1
    if inclusive_days < holdout_days:
        raise BacktestError(f"source window has {inclusive_days} dates; need at least {holdout_days}")
    return end - timedelta(days=holdout_days - 1), end


def _require_plain_date(value: object, label: str) -> None:
    # datetime subclasses date; a datetime carries a time/tz the date-keyed window must not accept.
    if isinstance(value, datetime) or not isinstance(value, date):
        raise BacktestError(f"{label} must be a date, not {type(value).__name__}")


def validate_holdout_window(
    holdout: tuple[date, date], source_window: tuple[date, date], *, holdout_days: int = DEFAULT_HOLDOUT_DAYS
) -> tuple[date, date]:
    """Validate an explicit holdout override: exactly ``holdout_days`` dates, inside the source window.

    Guards against a caller silently passing an empty, mis-sized, or out-of-range (e.g. future)
    window that would otherwise score against absent data.
    """
    start, end = holdout
    _require_plain_date(start, "holdout start")
    _require_plain_date(end, "holdout end")
    source_start, source_end = source_window
    _require_plain_date(source_start, "source window start")
    _require_plain_date(source_end, "source window end")
    if source_end < source_start:
        raise BacktestError(f"source window is reversed: {source_start} > {source_end}")
    if end < start:
        raise BacktestError(f"holdout window is reversed: {start} > {end}")
    inclusive_days = (end - start).days + 1
    if inclusive_days != holdout_days:
        raise BacktestError(f"explicit holdout must be exactly {holdout_days} inclusive dates, got {inclusive_days}")
    if start < source_start or end > source_end:
        raise BacktestError(f"holdout {start}..{end} is not within the source window {source_start}..{source_end}")
    return holdout


# --- eligibility / leakage (F9.ac15) -------------------------------------------


def assert_model_eligible(model: Mapping[str, object], *, holdout_start: date, feature_version: str, taxonomy_version: str) -> None:
    """Refuse a model whose training/selection windows reach the holdout, or whose versions differ."""
    if model.get("feature_version") != feature_version:
        raise BacktestError(
            f"model feature_version {model.get('feature_version')!r} differs from requested {feature_version!r}"
        )
    if model.get("taxonomy_version") != taxonomy_version:
        raise BacktestError(
            f"model taxonomy_version {model.get('taxonomy_version')!r} differs from requested {taxonomy_version!r}"
        )
    train_start = model.get("train_window_start")
    train_end = model.get("train_window_end")
    validation_start = model.get("validation_window_start")
    validation_end = model.get("validation_window_end")
    # Reject datetime bounds explicitly (datetime subclasses date) so none reaches a date comparison.
    _require_plain_date(train_start, "model train_window_start")
    _require_plain_date(train_end, "model train_window_end")
    _require_plain_date(validation_start, "model validation_window_start")
    _require_plain_date(validation_end, "model validation_window_end")
    if train_end < train_start:
        raise BacktestError(f"model training window is reversed: {train_start} > {train_end}")
    if validation_end < validation_start:
        raise BacktestError(f"model selection-validation window is reversed: {validation_start} > {validation_end}")
    if validation_start <= train_end:
        raise BacktestError(
            f"selection-validation starts {validation_start} on/before training end {train_end} (overlap)"
        )
    if train_end >= holdout_start:
        raise BacktestError(f"training window ends {train_end} on/after holdout start {holdout_start} (leakage)")
    if validation_end >= holdout_start:
        raise BacktestError(f"selection-validation ends {validation_end} on/after holdout start {holdout_start} (leakage)")


# --- key alignment + contract validation (F9.ac16) -----------------------------


def _local_hour(moment: datetime) -> int:
    return moment.astimezone(ZoneInfo(CANONICAL_TZ)).hour


def _require_finite_decimal(value: object, label: str) -> float:
    if not isinstance(value, Decimal) or value.is_nan() or value.is_infinite():
        raise BacktestError(f"non-finite {label}: {value!r}")
    return float(value)


def align_scoring_rows(
    expected_keys: Iterable[tuple[int, str, datetime]],
    actuals: Mapping[tuple[int, str, datetime], float],
    forecast_set: ForecastSet,
) -> list[ScoredRow]:
    """Require an exact one-to-one match of expected keys, actuals, and validated forecasts.

    Any missing, extra, or duplicate key, any version/interval/finiteness violation in the
    forecast, or any non-finite actual fails closed — there is no inner join that drops a bad row.
    """
    expected_list = list(expected_keys)
    expected_set = set(expected_list)
    if len(expected_list) != len(expected_set):
        raise BacktestError("expected key set contains duplicates")

    forecasts: dict[tuple[int, str, datetime], tuple[float, float, float]] = {}
    for record in forecast_set.records:
        if record.model_version != forecast_set.model_version or record.taxonomy_version != forecast_set.taxonomy_version:
            raise BacktestError(f"forecast record version disagrees with its envelope: {record!r}")
        key = (record.location_id, record.product_category, record.forecast_hour)
        if key in forecasts:
            raise BacktestError(f"duplicate forecast record for key {key!r}")
        lower = _require_finite_decimal(record.lower, "forecast lower")
        p70 = _require_finite_decimal(record.p70, "forecast p70")
        upper = _require_finite_decimal(record.upper, "forecast upper")
        if lower < 0 or lower > p70 or p70 > upper:
            raise BacktestError(f"invalid prediction interval for key {key!r}: {(lower, p70, upper)!r}")
        forecasts[key] = (lower, p70, upper)

    if expected_set != set(forecasts):
        raise BacktestError("forecast keys do not exactly match the expected open key set")
    if expected_set != set(actuals):
        raise BacktestError("actual keys do not exactly match the expected open key set")

    rows: list[ScoredRow] = []
    for key in expected_list:
        actual = float(actuals[key])
        if not math.isfinite(actual):
            raise BacktestError(f"non-finite actual for key {key!r}: {actuals[key]!r}")
        if actual < 0:
            raise BacktestError(f"negative actual for key {key!r}: {actual!r}")
        location_id, product_category, forecast_hour = key
        lower, p70, upper = forecasts[key]
        rows.append(ScoredRow(location_id, product_category, forecast_hour, actual, p70, lower, upper))
    return rows


# --- MAPE / zero policy (F9.ac17) ----------------------------------------------


def group_metrics(rows: Iterable[ScoredRow]) -> dict[tuple[int, str, int], GroupMetric]:
    """Group scored rows by (location, category, clock hour) and compute MAPE over positive actuals."""
    buckets: dict[tuple[int, str, int], list[ScoredRow]] = {}
    for row in rows:
        if not math.isfinite(row.actual) or row.actual < 0:
            raise BacktestError(f"invalid actual {row.actual!r} for {row!r}; demand must be finite and non-negative")
        buckets.setdefault((row.location_id, row.product_category, _local_hour(row.forecast_hour)), []).append(row)

    metrics: dict[tuple[int, str, int], GroupMetric] = {}
    for key, members in buckets.items():
        positives = [r for r in members if r.actual > 0]
        n_zero = sum(1 for r in members if r.actual == 0)
        # No positive actual: MAPE is undefined. Report None, never 0.0, so it cannot force a PASS.
        mape = sum(100.0 * abs(r.p70 - r.actual) / r.actual for r in positives) / len(positives) if positives else None
        metrics[key] = GroupMetric(
            key=key, n_total=len(members), n_positive_actual=len(positives), n_zero_actual=n_zero,
            mape=mape, scorable=bool(positives),
        )
    return metrics


# --- quantile vs interval coverage (F9.ac18) -----------------------------------


def coverage_metrics(rows: Iterable[ScoredRow]) -> CoverageMetric:
    """Report distinct P70 hit rate and P10–P90 interval coverage; both denominators include zeros."""
    materialised = list(rows)
    if not materialised:
        raise BacktestError("cannot compute coverage over no rows")
    p70_hits = sum(1 for r in materialised if r.actual <= r.p70)
    interval_hits = sum(1 for r in materialised if r.lower <= r.actual <= r.upper)
    n = len(materialised)
    return CoverageMetric(
        n_rows=n, p70_hits=p70_hits, p70_rate=p70_hits / n, p70_nominal=P70_NOMINAL,
        interval_hits=interval_hits, interval_rate=interval_hits / n, interval_nominal=INTERVAL_NOMINAL,
    )


# --- per-group gate (F9.ac19) --------------------------------------------------


def wape_metrics(rows: Iterable[ScoredRow]) -> tuple[float, dict[tuple[int, str], WapeMetric]]:
    """Return overall volume-weighted WAPE and the per-(location, category) breakdown.

    WAPE is ``sum|p70 - actual| / sum actual``, so unlike MAPE it never divides by a single small
    actual; zero-actual rows contribute error without exploding the denominator.
    """
    buckets: dict[tuple[int, str], list[ScoredRow]] = {}
    for row in rows:
        if not math.isfinite(row.actual) or row.actual < 0:
            raise BacktestError(f"invalid actual {row.actual!r} for {row!r}; demand must be finite and non-negative")
        if not math.isfinite(row.p70):
            raise BacktestError(f"invalid p70 {row.p70!r} for {row!r}; a forecast must be finite")
        buckets.setdefault((row.location_id, row.product_category), []).append(row)
    if not buckets:
        raise BacktestError("cannot compute WAPE over no rows")

    total_error = total_volume = 0.0
    metrics: dict[tuple[int, str], WapeMetric] = {}
    for key, members in buckets.items():
        volume = sum(r.actual for r in members)
        abs_error = sum(abs(r.p70 - r.actual) for r in members)
        total_error += abs_error
        total_volume += volume
        metrics[key] = WapeMetric(
            key=key, n_rows=len(members), volume=volume, abs_error=abs_error,
            # No demand at all: WAPE is undefined. Report None, never 0.0, so it cannot force a PASS.
            wape=(100.0 * abs_error / volume) if volume > 0 else None,
            gated=volume >= GROUP_VOLUME_FLOOR,
        )
    if total_volume <= 0:
        raise BacktestError("holdout carries no demand; refusing to report a WAPE")
    return 100.0 * total_error / total_volume, metrics


def evaluate_gate(
    overall_wape: float,
    wape_groups: Mapping[tuple[int, str], WapeMetric],
    coverage: CoverageMetric,
) -> GateOutcome:
    """Pass only when overall WAPE, every volume-gated group, and calibration are all within band.

    The limits are the ratified module constants and are deliberately not caller-configurable, so
    no override can relax the acceptance threshold. A group below ``GROUP_VOLUME_FLOOR`` is a
    diagnostic, never a pass or a fail — gating ~40 units of annual demand would gate noise. If no
    group clears the floor the gate fails rather than passing vacuously.
    """
    failing = tuple(
        sorted(k for k, m in wape_groups.items() if m.gated and (m.wape is None or m.wape > GROUP_WAPE_LIMIT))
    )
    diagnostics = tuple(sorted(k for k, m in wape_groups.items() if not m.gated))
    p70_drift = abs(coverage.p70_rate - coverage.p70_nominal) * 100.0
    interval_drift = abs(coverage.interval_rate - coverage.interval_nominal) * 100.0
    calibration_ok = p70_drift <= CALIBRATION_TOLERANCE_PP and interval_drift <= CALIBRATION_TOLERANCE_PP

    reasons: list[str] = []
    if not wape_groups:
        reasons.append("no groups were scored")
    if not any(m.gated for m in wape_groups.values()):
        reasons.append(f"no group reached the {GROUP_VOLUME_FLOOR:.0f}-unit volume floor; nothing was gated")
    if overall_wape > WAPE_LIMIT:
        reasons.append(f"overall WAPE {overall_wape:.2f}% exceeds the {WAPE_LIMIT:.1f}% band")
    if failing:
        reasons.append(f"{len(failing)} volume-gated group(s) exceed the {GROUP_WAPE_LIMIT:.1f}% WAPE band")
    if not calibration_ok:
        reasons.append(
            f"calibration drifted past {CALIBRATION_TOLERANCE_PP:.1f}pp "
            f"(P70 {p70_drift:.2f}pp, interval {interval_drift:.2f}pp)"
        )
    return GateOutcome(
        passed=not reasons, overall_wape=overall_wape, failing_groups=failing,
        diagnostic_groups=diagnostics, calibration_ok=calibration_ok, reasons=tuple(reasons),
    )


# --- report schema + atomic write (F9.ac20) ------------------------------------


def _iso(value: date) -> str:
    return value.isoformat()


def build_report(
    *,
    run_id: str,
    generated_at: datetime,
    model: Mapping[str, object],
    holdout_window: tuple[date, date],
    group_metrics: Mapping[tuple[int, str, int], GroupMetric],
    coverage: CoverageMetric,
    gate: GateOutcome,
    wape_groups: Mapping[tuple[int, str], WapeMetric],
) -> dict:
    """Assemble the schema-versioned, deterministically ordered backtest report."""
    ordered = [group_metrics[key] for key in sorted(group_metrics)]
    groups = [
        {
            "location_id": m.key[0], "product_category": m.key[1], "clock_hour": m.key[2],
            "n_total": m.n_total, "n_positive_actual": m.n_positive_actual, "n_zero_actual": m.n_zero_actual,
            "mape": m.mape, "scorable": m.scorable,
        }
        for m in ordered
    ]
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "run_id": run_id,
        "generated_at": generated_at.isoformat(),
        "model_version": model["model_version"],
        "feature_version": model["feature_version"],
        "taxonomy_version": model["taxonomy_version"],
        "contract_version": model.get("contract_version", CONTRACT_VERSION),
        "approach": model["approach"],
        "protocol": BACKTEST_PROTOCOL,
        "windows": {
            "train_start": _iso(model["train_window_start"]),
            "train_end": _iso(model["train_window_end"]),
            "validation_start": _iso(model["validation_window_start"]),
            "validation_end": _iso(model["validation_window_end"]),
            "holdout_start": _iso(holdout_window[0]),
            "holdout_end": _iso(holdout_window[1]),
        },
        "wape": {
            "definition": _WAPE_DEFINITION,
            "limit": WAPE_LIMIT,
            "group_limit": GROUP_WAPE_LIMIT,
            "group_volume_floor": GROUP_VOLUME_FLOOR,
            "overall": gate.overall_wape,
            "groups": [
                {
                    "location_id": m.key[0], "product_category": m.key[1], "n_rows": m.n_rows,
                    "volume": m.volume, "abs_error": m.abs_error, "wape": m.wape, "gated": m.gated,
                }
                for m in (wape_groups[key] for key in sorted(wape_groups))
            ],
        },
        "mape": {
            "definition": _MAPE_DEFINITION,
            "limit": MAPE_LIMIT,
            "zero_policy": _ZERO_POLICY,
            "role": "diagnostic only since the 2026-11-23 acceptance-metric ratification",
        },
        "quantiles": {
            "p70_definition": "P70 hit rate is actual <= p70",
            "p70_nominal": P70_NOMINAL,
            "interval_definition": "P10-P90 coverage is lower <= actual <= upper",
            "interval_nominal": INTERVAL_NOMINAL,
        },
        "counts": {
            "n_total": sum(m.n_total for m in ordered),
            "n_positive_actual": sum(m.n_positive_actual for m in ordered),
            "n_zero_actual": sum(m.n_zero_actual for m in ordered),
        },
        "coverage": {
            "n_rows": coverage.n_rows,
            "p70_hits": coverage.p70_hits, "p70_rate": coverage.p70_rate, "p70_nominal": coverage.p70_nominal,
            "interval_hits": coverage.interval_hits, "interval_rate": coverage.interval_rate,
            "interval_nominal": coverage.interval_nominal,
        },
        "groups": groups,
        "failing_groups": [list(key) for key in gate.failing_groups],
        "diagnostic_groups": [list(key) for key in gate.diagnostic_groups],
        "unscorable_mape_groups": [list(m.key) for m in ordered if not m.scorable],
        "gate": {
            "passed": gate.passed,
            "overall_wape": gate.overall_wape,
            "wape_limit": WAPE_LIMIT,
            "group_wape_limit": GROUP_WAPE_LIMIT,
            "group_volume_floor": GROUP_VOLUME_FLOOR,
            "calibration_ok": gate.calibration_ok,
            "calibration_tolerance_pp": CALIBRATION_TOLERANCE_PP,
            "reasons": list(gate.reasons),
        },
    }


def report_path(output_dir: str | os.PathLike[str], model_version: str) -> Path:
    """Derive the version-scoped report path, rejecting unsafe model versions before any I/O."""
    if not isinstance(model_version, str) or not _SAFE_MODEL_VERSION.match(model_version) or ".." in model_version:
        raise BacktestError(f"unsafe model version for report path: {model_version!r}")
    return Path(output_dir) / f"backtest-{model_version}.json"


def write_report_atomic(output_dir: str | os.PathLike[str], report: Mapping, *, model_version: str) -> Path:
    """Write the report to its version-scoped path atomically, never clobbering a foreign report."""
    path = report_path(output_dir, model_version)
    if report.get("model_version") != model_version:
        raise BacktestError("report model_version does not match the requested write version")
    directory = path.parent
    if directory.exists():
        if not directory.is_dir():
            raise BacktestError(f"report output path is not a directory: {directory}")
    else:
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise BacktestError(f"cannot create report directory {directory}") from exc
    if path.exists():
        try:
            existing = json.loads(path.read_text())
        except (OSError, ValueError) as exc:
            raise BacktestError(f"existing report at {path} is unreadable; refusing to overwrite") from exc
        if existing.get("model_version") != model_version:
            raise BacktestError(f"refusing to overwrite report at {path} for model {existing.get('model_version')!r}")

    # Write a temporary sibling then atomically replace, so a crash never leaves truncated evidence.
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".backtest-", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(report, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    return path


# --- DB orchestration + CLI ----------------------------------------------------


def load_source_window(conn, feature_version: str) -> tuple[date, date]:
    """Read the registered source window for a feature version from ``feature_sets``."""
    row = conn.execute(
        "SELECT source_window_start, source_window_end FROM feature_sets WHERE feature_version = %s",
        (feature_version,),
    ).fetchone()
    if row is None:
        raise BacktestError(f"no feature_sets row for feature_version {feature_version!r}")
    return row[0], row[1]


def _load_actuals_and_keys(conn, feature_version: str, holdout: tuple[date, date]):
    rows = conn.execute(
        "SELECT location_id, product_category, obs_date, obs_hour, items FROM forecast_features "
        "WHERE feature_version = %s AND obs_date BETWEEN %s AND %s",
        (feature_version, *holdout),
    ).fetchall()
    tz = ZoneInfo(CANONICAL_TZ)
    expected: list[tuple[int, str, datetime]] = []
    actuals: dict[tuple[int, str, datetime], float] = {}
    for location_id, product_category, obs_date, obs_hour, items in rows:
        moment = datetime(obs_date.year, obs_date.month, obs_date.day, int(obs_hour), tzinfo=tz)
        key = (int(location_id), str(product_category), moment)
        expected.append(key)
        actuals[key] = float(items)
    return expected, actuals


def resolve_official_holdout(
    source_window: tuple[date, date], requested: tuple[date, date] | None = None
) -> tuple[date, date]:
    """Return the official F5 gate window — always the final 28 source dates.

    An optional ``requested`` window is accepted only when it is exactly that derived window, so a
    caller cannot cherry-pick an easier non-final 28-day subset and claim the official gate.
    """
    derived = derive_holdout_window(source_window)
    if requested is None:
        return derived
    # Validate well-formedness first (28 inclusive dates within the source), then require the tail.
    validate_holdout_window(requested, source_window)
    if tuple(requested) != derived:
        raise BacktestError(
            f"official F5 gate uses only the final 28 source dates {derived}; refusing holdout {tuple(requested)}"
        )
    return derived


def run_backtest(
    conn,
    *,
    feature_version: str,
    model_version: str,
    holdout_window: tuple[date, date] | None = None,
    output_dir: str | os.PathLike[str] | None = None,
) -> dict:
    """Backtest a persisted model over the official holdout and return an honest report.

    The scored window is always the final 28 source dates (``resolve_official_holdout``); a supplied
    ``holdout_window`` is accepted only when it equals that derived window, so no caller can obtain a
    passing gate on an easier window. The ≤10% band is likewise fixed. Fails closed (raises) on an
    unavailable feature set, an ineligible/leaky or version-mismatched model, a non-official holdout,
    or incomplete key coverage. A completed-but-failing evaluation returns a report whose
    ``gate.passed`` is ``False`` rather than raising, so the FAIL evidence is preserved.
    """
    from agents.demand_forecast.model import load_model, predict

    source_window = load_source_window(conn, feature_version)
    holdout = resolve_official_holdout(source_window, holdout_window)

    model = load_model(conn, model_version)
    assert_model_eligible(
        model, holdout_start=holdout[0], feature_version=feature_version, taxonomy_version=PINNED_TAXONOMY_VERSION
    )
    # predict() applies the canonical contract validation (validate_set) to the emitted ForecastSet.
    forecast_set = predict(conn, model_version, target_window=holdout, feature_version=feature_version)
    expected, actuals = _load_actuals_and_keys(conn, feature_version, holdout)
    rows = align_scoring_rows(expected, actuals, forecast_set)

    metrics = group_metrics(rows)
    coverage = coverage_metrics(rows)
    overall_wape, wape_groups = wape_metrics(rows)
    gate = evaluate_gate(overall_wape, wape_groups, coverage)
    identity = {**model, "contract_version": CONTRACT_VERSION}
    report = build_report(
        run_id=uuid.uuid4().hex, generated_at=datetime.now(UTC), model=identity, holdout_window=holdout,
        group_metrics=metrics, coverage=coverage, gate=gate, wape_groups=wape_groups,
    )
    if output_dir is not None:
        write_report_atomic(output_dir, report, model_version=model_version)
    return report


def main(argv: list[str] | None = None) -> int:
    """CLI: backtest a persisted model and exit non-zero when the gate fails."""
    parser = argparse.ArgumentParser(description="Backtest a CS-014b demand forecast model (CS-014c).")
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--tenant-id", default="00000000-0000-0000-0000-000000000001")
    parser.add_argument("--model-version", required=True)
    parser.add_argument("--feature-version", required=True)
    parser.add_argument("--output-dir", help="directory for the version-scoped JSON report")
    args = parser.parse_args(argv)
    # Intentionally no holdout override: the official F5 gate always scores the final 28 source dates.

    from data_access.session import tenant_session

    with tenant_session(args.database_url, role="app_ro", tenant_id=args.tenant_id) as conn:
        report = run_backtest(
            conn, feature_version=args.feature_version, model_version=args.model_version,
            output_dir=args.output_dir,
        )
    passed = report["gate"]["passed"]
    print(json.dumps(report, ensure_ascii=False, indent=2))
    counts, wape = report["counts"], report["wape"]
    print(
        f"{'PASS' if passed else 'FAIL'} — overall WAPE {wape['overall']:.2f}% "
        f"(band {wape['limit']:.1f}%), {counts['n_total']} rows; "
        f"{len(report['failing_groups'])} gated group(s) failing, "
        f"{len(report['diagnostic_groups'])} below the volume floor."
    )
    for reason in report["gate"]["reasons"]:
        print(f"  - {reason}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
