"""CS-014c forecast-backtest tests: offline contracts plus rollback-only DB coverage.

These are the failing-first (test-first) contracts for F9.ac14–F9.ac20. They import the
not-yet-written ``agents.demand_forecast.backtest`` module, so collection fails red until the
backtest implementation (phase B) exists. The assertions encode the honest pass/fail semantics
the SDD requires: a group with no positive actual is unscorable and blocks the gate, MAPE is
measured per store/category/clock-hour over positive actuals only, P70 hit rate and P10–P90
interval coverage are distinct measures, leakage/version mismatches fail closed, and the DB
integration test asserts the gate outcome it *computes*, never that real data passes.
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

SCHEDULER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCHEDULER_ROOT))

# Imported from the module under construction; absence is the documented test-first red.
from agents.demand_forecast.backtest import (  # noqa: E402
    BACKTEST_PROTOCOL,
    DEFAULT_HOLDOUT_DAYS,
    INTERVAL_NOMINAL,
    MAPE_LIMIT,
    P70_NOMINAL,
    REPORT_SCHEMA_VERSION,
    BacktestError,
    ScoredRow,
    align_scoring_rows,
    assert_model_eligible,
    build_report,
    coverage_metrics,
    derive_holdout_window,
    evaluate_gate,
    group_metrics,
    report_path,
    resolve_official_holdout,
    run_backtest,
    validate_holdout_window,
    write_report_atomic,
)
from agents.demand_forecast.feature_build import FEATURE_VERSION, build_feature_set  # noqa: E402
from contracts.forecast import (  # noqa: E402
    CANONICAL_TZ,
    CONTRACT_VERSION,
    PINNED_TAXONOMY_VERSION,
    ForecastRecord,
    ForecastSet,
)

TENANT_ID = "00000000-0000-0000-0000-000000000001"
DSN = os.environ.get("DATABASE_URL", "postgresql://coffeeshop:coffeeshop@localhost:5432/coffeeshop")
TZ = ZoneInfo(CANONICAL_TZ)
SOURCE_WINDOW_H1 = (date(2023, 1, 1), date(2023, 6, 30))
HOLDOUT_H1 = (date(2023, 6, 3), date(2023, 6, 30))


def _hour(day: int, hour: int, *, month: int = 6, year: int = 2023) -> datetime:
    return datetime(year, month, day, hour, tzinfo=TZ)


def _scored(loc, cat, day, hour, actual, lower, p70, upper) -> ScoredRow:
    return ScoredRow(
        location_id=loc, product_category=cat, forecast_hour=_hour(day, hour),
        actual=float(actual), lower=float(lower), p70=float(p70), upper=float(upper),
    )


def _record(loc, cat, dt, lower, p70, upper, *, model="m-cs014c", tax=PINNED_TAXONOMY_VERSION) -> ForecastRecord:
    money = lambda v: Decimal(str(v))  # noqa: E731
    return ForecastRecord(loc, cat, dt, money(p70), money(lower), money(upper), model, tax)


def _forecast_set(records, *, model="m-cs014c", tax=PINNED_TAXONOMY_VERSION) -> ForecastSet:
    return ForecastSet(CONTRACT_VERSION, tax, model, datetime.now(UTC), tuple(records))


def _eligible_model(**overrides) -> dict:
    model = {
        "model_version": "m-cs014c", "feature_version": FEATURE_VERSION,
        "taxonomy_version": PINNED_TAXONOMY_VERSION, "contract_version": CONTRACT_VERSION,
        "approach": "empirical_quantile",
        "train_window_start": date(2023, 1, 1), "train_window_end": date(2023, 5, 20),
        "validation_window_start": date(2023, 5, 21), "validation_window_end": date(2023, 6, 2),
    }
    model.update(overrides)
    return model


class HoldoutWindowTests(unittest.TestCase):
    # spec: F9.ac14
    def test_ac14_holdout_is_last_28_source_dates(self):
        self.assertEqual(DEFAULT_HOLDOUT_DAYS, 28)
        # The canonical H1-2023 source window yields 2023-06-03 .. 2023-06-30 inclusive.
        self.assertEqual(derive_holdout_window(SOURCE_WINDOW_H1), HOLDOUT_H1)
        start, end = derive_holdout_window(SOURCE_WINDOW_H1)
        self.assertEqual((end - start).days + 1, 28)
        # Not coupled to wall-clock: a future-year window and a leap-day-spanning window both work.
        self.assertEqual(
            derive_holdout_window((date(2099, 1, 1), date(2099, 3, 31))), (date(2099, 3, 4), date(2099, 3, 31))
        )
        leap_start, leap_end = derive_holdout_window((date(2024, 1, 1), date(2024, 3, 15)))
        self.assertEqual((leap_start, leap_end), (date(2024, 2, 17), date(2024, 3, 15)))
        self.assertIn(date(2024, 2, 29), {leap_start + timedelta(days=i) for i in range(28)})
        # A window exactly 28 days long is the minimum accepted case.
        self.assertEqual(
            derive_holdout_window((date(2023, 6, 3), date(2023, 6, 30))), (date(2023, 6, 3), date(2023, 6, 30))
        )

    # spec: F9.ac14
    def test_ac14_short_window_rejected(self):
        # Fewer than 28 inclusive dates: fail loud, never silently pad.
        with self.assertRaises(BacktestError):
            derive_holdout_window((date(2023, 6, 4), date(2023, 6, 30)))
        # Reversed window.
        with self.assertRaises(BacktestError):
            derive_holdout_window((date(2023, 6, 30), date(2023, 6, 3)))
        # A single day.
        with self.assertRaises(BacktestError):
            derive_holdout_window((date(2023, 6, 30), date(2023, 6, 30)))
        # Non-positive holdout length is nonsensical and fails loud.
        with self.assertRaises(BacktestError):
            derive_holdout_window(SOURCE_WINDOW_H1, holdout_days=0)
        with self.assertRaises(BacktestError):
            derive_holdout_window(SOURCE_WINDOW_H1, holdout_days=-5)
        # datetime subclasses date; a datetime carries a time/tz the date-keyed window must not accept.
        with self.assertRaises(BacktestError):
            derive_holdout_window((datetime(2023, 1, 1, tzinfo=TZ), datetime(2023, 6, 30, tzinfo=TZ)))

    # spec: F9.ac14
    def test_ac14_explicit_holdout_must_be_28_within_source(self):
        # An explicit override must be exactly 28 inclusive dates and lie inside the source window.
        self.assertEqual(validate_holdout_window(HOLDOUT_H1, SOURCE_WINDOW_H1), HOLDOUT_H1)
        # Wrong length (27 inclusive dates).
        with self.assertRaises(BacktestError):
            validate_holdout_window((date(2023, 6, 4), date(2023, 6, 30)), SOURCE_WINDOW_H1)
        # 28 days but running past the registered source end (silent future window).
        with self.assertRaises(BacktestError):
            validate_holdout_window((date(2023, 7, 4), date(2023, 7, 31)), SOURCE_WINDOW_H1)
        # 28 days but starting before the registered source start.
        with self.assertRaises(BacktestError):
            validate_holdout_window((date(2022, 12, 4), date(2022, 12, 31)), SOURCE_WINDOW_H1)
        # Reversed override.
        with self.assertRaises(BacktestError):
            validate_holdout_window((date(2023, 6, 30), date(2023, 6, 3)), SOURCE_WINDOW_H1)
        # A malformed (reversed) source window is rejected before any containment comparison.
        with self.assertRaises(BacktestError):
            validate_holdout_window(HOLDOUT_H1, (date(2023, 6, 30), date(2023, 1, 1)))

    # spec: F9.ac14
    def test_ac14_official_gate_uses_only_final_28(self):
        # The official F5 gate window is always the final 28 source dates.
        self.assertEqual(resolve_official_holdout(SOURCE_WINDOW_H1), HOLDOUT_H1)
        self.assertEqual(resolve_official_holdout(SOURCE_WINDOW_H1, HOLDOUT_H1), HOLDOUT_H1)
        # A well-formed but non-final 28-day window inside the source cannot stand in for the official gate.
        with self.assertRaises(BacktestError):
            resolve_official_holdout(SOURCE_WINDOW_H1, (date(2023, 6, 2), date(2023, 6, 29)))

    # spec: F9.ac14
    def test_ac14_cli_has_no_holdout_override(self):
        # The CLI must not expose a holdout override that could cherry-pick an easier window.
        from agents.demand_forecast import backtest as bt

        with self.assertRaises(SystemExit):
            bt.main(["--model-version", "m", "--feature-version", "f",
                     "--holdout-start", "2023-06-02", "--holdout-end", "2023-06-29"])


class LeakageTests(unittest.TestCase):
    # spec: F9.ac15
    def test_ac15_temporal_or_version_leakage_rejected(self):
        holdout_start = HOLDOUT_H1[0]
        kw = dict(holdout_start=holdout_start, feature_version=FEATURE_VERSION, taxonomy_version=PINNED_TAXONOMY_VERSION)
        # Eligible model: train and selection-validation both finish before the holdout starts.
        assert_model_eligible(_eligible_model(), **kw)
        # Training window reaches into the holdout.
        with self.assertRaises(BacktestError):
            assert_model_eligible(_eligible_model(train_window_end=holdout_start), **kw)
        # Selection-validation window reaches the holdout start (>= holdout_start is leakage).
        with self.assertRaises(BacktestError):
            assert_model_eligible(_eligible_model(validation_window_end=holdout_start), **kw)
        with self.assertRaises(BacktestError):
            assert_model_eligible(_eligible_model(validation_window_end=date(2023, 6, 15)), **kw)
        # Feature-version mismatch between the model and the requested feature set.
        with self.assertRaises(BacktestError):
            assert_model_eligible(_eligible_model(feature_version="other-features"), **kw)
        # Taxonomy-version mismatch.
        with self.assertRaises(BacktestError):
            assert_model_eligible(_eligible_model(taxonomy_version="TAX-OTHER"), **kw)
        # Reversed training window.
        with self.assertRaises(BacktestError):
            assert_model_eligible(
                _eligible_model(train_window_start=date(2023, 5, 20), train_window_end=date(2023, 1, 1)), **kw
            )
        # Reversed selection-validation window.
        with self.assertRaises(BacktestError):
            assert_model_eligible(
                _eligible_model(validation_window_start=date(2023, 6, 2), validation_window_end=date(2023, 5, 21)), **kw
            )
        # Selection-validation that starts on/before the training window end overlaps training.
        with self.assertRaises(BacktestError):
            assert_model_eligible(_eligible_model(validation_window_start=date(2023, 5, 20)), **kw)
        # datetime bounds (datetime subclasses date) are rejected, not compared against a date holdout.
        with self.assertRaises(BacktestError):
            assert_model_eligible(_eligible_model(train_window_end=datetime(2023, 5, 20, tzinfo=TZ)), **kw)
        with self.assertRaises(BacktestError):
            assert_model_eligible(_eligible_model(validation_window_end=datetime(2023, 6, 2, tzinfo=TZ)), **kw)


class KeyAlignmentTests(unittest.TestCase):
    def _complete_case(self):
        k1 = (3, "Coffee", _hour(3, 8))
        k2 = (3, "Coffee", _hour(3, 9))
        expected = [k1, k2]
        actuals = {k1: 10.0, k2: 0.0}
        fs = _forecast_set([
            _record(3, "Coffee", _hour(3, 8), 1, 9, 15),
            _record(3, "Coffee", _hour(3, 9), 0, 2, 5),
        ])
        return expected, actuals, fs

    # spec: F9.ac16
    def test_ac16_key_alignment_fails_closed(self):
        expected, actuals, fs = self._complete_case()
        rows = align_scoring_rows(expected, actuals, fs)
        self.assertEqual(len(rows), 2)
        self.assertEqual({(r.location_id, r.product_category, r.forecast_hour) for r in rows}, set(expected))

        # Missing actual for an expected/forecast key.
        with self.assertRaises(BacktestError):
            align_scoring_rows(expected, {expected[0]: 10.0}, fs)
        # Extra forecast record not in the expected key set.
        extra = _forecast_set([*fs.records, _record(5, "Tea", _hour(3, 10), 0, 1, 2)])
        with self.assertRaises(BacktestError):
            align_scoring_rows(expected, actuals, extra)
        # Missing forecast for an expected key (short forecast set).
        short = _forecast_set([fs.records[0]])
        with self.assertRaises(BacktestError):
            align_scoring_rows(expected, actuals, short)
        # Duplicate expected key fails closed rather than scoring twice.
        with self.assertRaises(BacktestError):
            align_scoring_rows([expected[0], expected[0], expected[1]], actuals, fs)
        # Duplicate forecast record for one key fails closed (contract uniqueness, not last-wins).
        dup_forecast = _forecast_set([fs.records[0], fs.records[0], fs.records[1]])
        with self.assertRaises(BacktestError):
            align_scoring_rows(expected, actuals, dup_forecast)
        # Extra actual key beyond the expected/forecast key set fails closed.
        extra_actual = {**actuals, (9, "Latte", _hour(3, 11)): 4.0}
        with self.assertRaises(BacktestError):
            align_scoring_rows(expected, extra_actual, fs)
        # Invalid interval (crossed lower > p70 > upper) fails closed rather than scoring a bad row.
        bad_interval = _forecast_set([_record(3, "Coffee", _hour(3, 8), 15, 9, 1), fs.records[1]])
        with self.assertRaises(BacktestError):
            align_scoring_rows(expected, actuals, bad_interval)
        # Record whose model/taxonomy version disagrees with the envelope fails closed.
        mismatched = ForecastSet(
            CONTRACT_VERSION, PINNED_TAXONOMY_VERSION, "m-cs014c", datetime.now(UTC),
            (_record(3, "Coffee", _hour(3, 8), 1, 9, 15, tax="TAX-OTHER"), fs.records[1]),
        )
        with self.assertRaises(BacktestError):
            align_scoring_rows(expected, actuals, mismatched)
        # Non-finite actual fails closed.
        with self.assertRaises(BacktestError):
            align_scoring_rows(expected, {expected[0]: float("nan"), expected[1]: 0.0}, fs)
        # Negative actual fails closed rather than being silently excluded from the MAPE denominator.
        with self.assertRaises(BacktestError):
            align_scoring_rows(expected, {expected[0]: -1.0, expected[1]: 0.0}, fs)
        # Non-finite forecast value (NaN p70) fails closed.
        nan_forecast = _forecast_set([
            _record(3, "Coffee", _hour(3, 8), 1, Decimal("NaN"), 15), fs.records[1],
        ])
        with self.assertRaises(BacktestError):
            align_scoring_rows(expected, actuals, nan_forecast)


class MapeAndGroupTests(unittest.TestCase):
    # spec: F9.ac17
    def test_ac17_mape_zero_actual_policy_and_group_counts(self):
        # One store/category/clock-hour group across three dates: two positive actuals and one zero.
        rows = [
            _scored(3, "Coffee", 3, 8, actual=10, lower=1, p70=11, upper=15),  # 100*1/10 = 10%
            _scored(3, "Coffee", 10, 8, actual=20, lower=1, p70=18, upper=25),  # 100*2/20 = 10%
            _scored(3, "Coffee", 17, 8, actual=0, lower=0, p70=1, upper=3),  # zero: excluded from MAPE, still counted
        ]
        metrics = group_metrics(rows)
        self.assertEqual(set(metrics), {(3, "Coffee", 8)})
        group = metrics[(3, "Coffee", 8)]
        self.assertEqual(group.n_total, 3)
        self.assertEqual(group.n_positive_actual, 2)
        self.assertEqual(group.n_zero_actual, 1)
        self.assertTrue(group.scorable)
        self.assertAlmostEqual(group.mape, 10.0)

    # spec: F9.ac17
    def test_ac17_no_positive_actual_is_unscorable(self):
        rows = [
            _scored(5, "Tea", 3, 9, actual=0, lower=0, p70=1, upper=2),
            _scored(5, "Tea", 10, 9, actual=0, lower=0, p70=0, upper=1),
        ]
        group = group_metrics(rows)[(5, "Tea", 9)]
        self.assertEqual(group.n_total, 2)
        self.assertEqual(group.n_positive_actual, 0)
        self.assertEqual(group.n_zero_actual, 2)
        self.assertFalse(group.scorable)
        # An unscorable group reports no MAPE; it must not be coerced to 0.0 to force a pass.
        self.assertIsNone(group.mape)

    # spec: F9.ac17
    def test_ac17_negative_actual_rejected(self):
        # A negative actual is invalid demand; it must fail loud, not be dropped from the denominator.
        with self.assertRaises(BacktestError):
            group_metrics([_scored(3, "Coffee", 3, 8, actual=-1, lower=0, p70=1, upper=2)])


class CoverageTests(unittest.TestCase):
    # spec: F9.ac18
    def test_ac18_quantile_and_interval_coverage_are_distinct(self):
        rows = [
            _scored(3, "Coffee", 3, 8, actual=0, lower=0, p70=2, upper=5),   # p70 hit, interval hit
            _scored(3, "Coffee", 4, 8, actual=3, lower=1, p70=2, upper=5),   # p70 miss, interval hit
            _scored(3, "Coffee", 5, 8, actual=4, lower=1, p70=5, upper=6),   # p70 hit, interval hit
            _scored(3, "Coffee", 6, 8, actual=10, lower=1, p70=5, upper=6),  # p70 miss, interval miss
            _scored(3, "Coffee", 7, 8, actual=0, lower=0, p70=1, upper=3),   # p70 hit, interval hit
        ]
        cov = coverage_metrics(rows)
        # Both denominators include the zero-actual rows (all five scored rows).
        self.assertEqual(cov.n_rows, 5)
        self.assertEqual(cov.p70_hits, 3)
        self.assertEqual(cov.interval_hits, 4)
        self.assertAlmostEqual(cov.p70_rate, 0.6)
        self.assertAlmostEqual(cov.interval_rate, 0.8)
        # The two measures are nominally different and must not be conflated.
        self.assertEqual(cov.p70_nominal, P70_NOMINAL)
        self.assertEqual(cov.interval_nominal, INTERVAL_NOMINAL)
        self.assertEqual((P70_NOMINAL, INTERVAL_NOMINAL), (0.70, 0.80))
        self.assertNotAlmostEqual(cov.p70_rate, cov.interval_rate)


class GateTests(unittest.TestCase):
    # spec: F9.ac19
    def test_ac19_each_group_must_meet_band(self):
        passing = group_metrics([
            _scored(3, "Coffee", 3, 8, actual=10, lower=1, p70=10.5, upper=15),  # 5%
            _scored(5, "Tea", 3, 9, actual=20, lower=1, p70=21, upper=25),       # 5%
        ])
        outcome = evaluate_gate(passing)
        self.assertTrue(outcome.passed)
        self.assertEqual(outcome.failing_groups, ())
        self.assertEqual(outcome.unscorable_groups, ())

        # One group at 8% (pass), one at 20% (fail): a good aggregate cannot mask the failing group.
        mixed = group_metrics([
            _scored(3, "Coffee", 3, 8, actual=100, lower=1, p70=108, upper=150),  # 8%
            _scored(5, "Tea", 3, 9, actual=100, lower=1, p70=120, upper=150),     # 20%
        ])
        failed = evaluate_gate(mixed)
        self.assertFalse(failed.passed)
        self.assertIn((5, "Tea", 9), failed.failing_groups)
        self.assertNotIn((3, "Coffee", 8), failed.failing_groups)

    # spec: F9.ac19
    def test_ac19_unscorable_group_fails_gate(self):
        metrics = group_metrics([
            _scored(3, "Coffee", 3, 8, actual=10, lower=1, p70=10.5, upper=15),  # scorable, 5%
            _scored(5, "Tea", 3, 9, actual=0, lower=0, p70=1, upper=2),          # unscorable (all zero)
        ])
        outcome = evaluate_gate(metrics)
        self.assertFalse(outcome.passed)
        self.assertIn((5, "Tea", 9), outcome.unscorable_groups)

    # spec: F9.ac19
    def test_ac19_mape_limit_cannot_be_weakened(self):
        # A group at 50% MAPE fails the locked F5 ≤10% band.
        metrics = group_metrics([_scored(3, "Coffee", 3, 8, actual=100, lower=1, p70=150, upper=200)])
        self.assertFalse(evaluate_gate(metrics).passed)
        # No caller-supplied threshold may relax the gate — the override does not exist.
        with self.assertRaises(TypeError):
            evaluate_gate(metrics, mape_limit=1000.0)
        # The report always records the authoritative fixed 10% limit, and build_report accepts no override.
        report = build_report(
            run_id="r", generated_at=datetime(2026, 11, 21, tzinfo=UTC), model=_eligible_model(),
            holdout_window=HOLDOUT_H1, group_metrics=metrics,
            coverage=coverage_metrics([_scored(3, "Coffee", 3, 8, actual=100, lower=1, p70=150, upper=200)]),
            gate=evaluate_gate(metrics),
        )
        self.assertEqual(report["mape"]["limit"], 10.0)
        self.assertFalse(report["gate"]["passed"])
        with self.assertRaises(TypeError):
            build_report(
                run_id="r", generated_at=datetime(2026, 11, 21, tzinfo=UTC), model=_eligible_model(),
                holdout_window=HOLDOUT_H1, group_metrics=metrics,
                coverage=coverage_metrics([_scored(3, "Coffee", 3, 8, actual=100, lower=1, p70=150, upper=200)]),
                gate=evaluate_gate(metrics), mape_limit=1000.0,
            )


class ReportTests(unittest.TestCase):
    def _report(self):
        rows = [
            _scored(3, "Coffee", 3, 8, actual=10, lower=1, p70=10.5, upper=15),  # scorable 5%
            _scored(3, "Coffee", 10, 8, actual=20, lower=1, p70=20, upper=25),   # scorable 0%
            _scored(5, "Tea", 3, 9, actual=0, lower=0, p70=1, upper=2),          # unscorable
        ]
        gm = group_metrics(rows)
        return build_report(
            run_id="run-123",
            generated_at=datetime(2026, 11, 21, 12, 0, tzinfo=UTC),
            model=_eligible_model(),
            holdout_window=HOLDOUT_H1,
            group_metrics=gm,
            coverage=coverage_metrics(rows),
            gate=evaluate_gate(gm),
        )

    # spec: F9.ac20
    def test_ac20_report_schema_and_identity(self):
        report = self._report()
        self.assertEqual(report["schema_version"], REPORT_SCHEMA_VERSION)
        self.assertEqual(report["run_id"], "run-123")
        self.assertEqual(report["model_version"], "m-cs014c")
        self.assertEqual(report["feature_version"], FEATURE_VERSION)
        self.assertEqual(report["taxonomy_version"], PINNED_TAXONOMY_VERSION)
        self.assertEqual(report["contract_version"], CONTRACT_VERSION)
        # Model approach and the rolling-origin protocol name are both recorded (distinct identities).
        self.assertEqual(report["approach"], "empirical_quantile")
        self.assertEqual(report["protocol"], BACKTEST_PROTOCOL)
        self.assertTrue(BACKTEST_PROTOCOL)
        # Train / validation / holdout identity is recorded.
        windows = report["windows"]
        self.assertEqual(windows["train_start"], "2023-01-01")
        self.assertEqual(windows["validation_end"], "2023-06-02")
        self.assertEqual(windows["holdout_start"], "2023-06-03")
        self.assertEqual(windows["holdout_end"], "2023-06-30")
        # Metric definitions and the limit are explicit, not implied.
        self.assertEqual(report["mape"]["limit"], MAPE_LIMIT)
        self.assertTrue(report["mape"]["definition"])
        self.assertTrue(report["mape"]["zero_policy"])
        self.assertEqual(report["quantiles"]["p70_nominal"], P70_NOMINAL)
        self.assertEqual(report["quantiles"]["interval_nominal"], INTERVAL_NOMINAL)
        # Aggregate counts are present and consistent.
        self.assertEqual(report["counts"]["n_total"], 3)
        self.assertEqual(report["counts"]["n_positive_actual"], 2)
        self.assertEqual(report["counts"]["n_zero_actual"], 1)
        # Every scored group is itemised with its own counts; the unscorable one is listed.
        group_keys = {(g["location_id"], g["product_category"], g["clock_hour"]) for g in report["groups"]}
        self.assertEqual(group_keys, {(3, "Coffee", 8), (5, "Tea", 9)})
        self.assertIn([5, "Tea", 9], report["unscorable_groups"])
        # An unscorable group blocks the gate even though the scorable group passes.
        self.assertFalse(report["gate"]["passed"])
        # Groups carry aggregate counts only, not row-level transaction data / PII.
        for group in report["groups"]:
            self.assertEqual(set(group) >= {"n_total", "n_positive_actual", "n_zero_actual", "mape", "scorable"}, True)
            self.assertNotIn("actuals", group)
            self.assertNotIn("rows", group)
        # Deterministic key order: re-serialising identical inputs yields byte-identical JSON.
        self.assertEqual(json.dumps(report), json.dumps(self._report()))

    # spec: F9.ac20
    def test_ac20_atomic_report_write(self):
        report = self._report()
        with TemporaryDirectory() as tmp:
            out = Path(tmp)
            written = write_report_atomic(out, report, model_version="m-cs014c")
            self.assertTrue(written.exists())
            self.assertEqual(json.loads(written.read_text()), report)
            # Version-scoped: a different model version is a different file, never an overwrite.
            other = dict(report, model_version="m-other")
            other_path = write_report_atomic(out, other, model_version="m-other")
            self.assertNotEqual(written, other_path)
            self.assertEqual(json.loads(written.read_text())["model_version"], "m-cs014c")
            # Atomic write leaves no temporary sibling behind on success.
            self.assertEqual(sorted(p.name for p in out.iterdir()), sorted([written.name, other_path.name]))
            # Re-writing the same version in place is allowed (idempotent replace of the matching report).
            rewritten = write_report_atomic(out, report, model_version="m-cs014c")
            self.assertEqual(rewritten, written)
            # Refuse to clobber a destination whose on-disk report embeds a DIFFERENT model_version:
            # a stray/foreign report at the version-scoped path must not be silently overwritten.
            foreign = dict(report, model_version="m-stale")
            written.write_text(json.dumps(foreign))
            with self.assertRaises(BacktestError):
                write_report_atomic(out, report, model_version="m-cs014c")
            # The refusal left the foreign file intact (no partial/truncated evidence).
            self.assertEqual(json.loads(written.read_text())["model_version"], "m-stale")
            # Unsafe model versions are rejected before any path is derived (no traversal / separators / blanks).
            for unsafe in ("../evil", "a/b", "..", "", "   ", "with\x00null"):
                with self.assertRaises(BacktestError):
                    report_path(out, unsafe)

    # spec: F9.ac20
    def test_ac20_output_dir_created_or_fails(self):
        report = self._report()
        with TemporaryDirectory() as tmp:
            # A not-yet-existing nested output directory is created rather than crashing.
            nested = Path(tmp) / "reports" / "cs014c"
            written = write_report_atomic(nested, report, model_version="m-cs014c")
            self.assertTrue(written.exists())
            self.assertEqual(written.parent, nested)
            # A destination whose parent is a regular file fails with a specific BacktestError.
            a_file = Path(tmp) / "afile"
            a_file.write_text("x")
            with self.assertRaises(BacktestError):
                write_report_atomic(a_file, report, model_version="m-cs014c")


_SKIP = None
try:
    import psycopg

    with psycopg.connect(DSN, connect_timeout=3) as _connection:
        _connection.execute("SELECT 1")
except Exception as exc:  # pragma: no cover - environment-dependent skip
    _SKIP = f"psycopg or database unavailable: {exc}"


class _Rollback(Exception):
    """Sentinel to leave the database unchanged after an integration assertion."""


@unittest.skipIf(_SKIP, _SKIP or "")
class BacktestDatabaseTests(unittest.TestCase):
    """Build features, train a strict outer-split model, and backtest inside a rollback-only txn."""

    # spec: F9.ac14
    # spec: F9.ac15
    # spec: F9.ac16
    # spec: F9.ac17
    # spec: F9.ac18
    # spec: F9.ac19
    # spec: F9.ac20
    def test_db_backtest_gate_is_honest(self):
        from agents.demand_forecast.backtest import load_source_window
        from agents.demand_forecast.model import train
        from data_access.session import open_session_context

        with psycopg.connect(DSN) as conn:
            try:
                with conn.transaction():
                    open_session_context(conn, role="app_rw", tenant_id=TENANT_ID)
                    build_feature_set(conn, feature_version="test-cs014c")
                    holdout = derive_holdout_window(load_source_window(conn, "test-cs014c"))
                    self.assertEqual(holdout, HOLDOUT_H1)
                    # Train a model whose train AND selection-validation windows finish before the holdout.
                    train(conn, model_version="test-cs014c", feature_version="test-cs014c",
                          window=(date(2023, 1, 1), holdout[0] - timedelta(days=1)), validation_days=7)
                    report = run_backtest(conn, feature_version="test-cs014c",
                                          model_version="test-cs014c", holdout_window=holdout)
                    # Persisted provenance surfaces in the report.
                    self.assertEqual(report["feature_version"], "test-cs014c")
                    self.assertEqual(report["windows"]["holdout_start"], holdout[0].isoformat())
                    self.assertGreater(report["counts"]["n_total"], 0)
                    # Honest gate: PASS iff every group is scorable and within the band. Never assume real data passes.
                    groups = report["groups"]
                    expected_pass = bool(groups) and all(
                        g["scorable"] and g["mape"] is not None and g["mape"] <= MAPE_LIMIT for g in groups
                    )
                    self.assertEqual(report["gate"]["passed"], expected_pass)
                    # A model whose selection-validation reaches the holdout is refused before scoring (no PASS).
                    train(conn, model_version="test-cs014c-leaky", feature_version="test-cs014c",
                          window=(date(2023, 1, 1), date(2023, 6, 30)), validation_days=14)
                    with self.assertRaises(BacktestError):
                        run_backtest(conn, feature_version="test-cs014c",
                                     model_version="test-cs014c-leaky", holdout_window=holdout)
                    raise _Rollback
            except _Rollback:
                pass


if __name__ == "__main__":
    unittest.main()
