"""Regression coverage for BUG-073's closure-scoped compliance snapshots."""

from __future__ import annotations

import os
import sys
import unittest
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents.scheduler import constraints  # noqa: E402
from agents.scheduler.inputs import EmployeeInput, SolverInputs  # noqa: E402
from agents.scheduler.shifts import ShiftSlot  # noqa: E402
from compliance.engine import (  # noqa: E402
    DayFacts,
    Employee,
    Proposal,
    Rule,
    Snapshot,
    Waiver,
    load_snapshot,
)

TENANT = "00000000-0000-0000-0000-000000000001"
DAY = date(2026, 12, 7)


def _employee(employee_id: int) -> EmployeeInput:
    return EmployeeInput(employee_id, "Barista", 3, 3, True, True, True, True, Decimal("16"), "open", 0, 40,
                         date(1990, 1, 1), False, None, "", False)


def _inputs() -> SolverInputs:
    # The adapter only uses employees and target_window, so a deliberately inert object keeps this unit test offline.
    return SolverInputs((), (), (), (), (), (), (), None, (_employee(1), _employee(2)), (), None, (DAY, DAY), "tax",
                        frozenset(), frozenset(), frozenset())


def _snapshot(employee_id: int) -> Snapshot:
    allowed_role = Rule("ROLE", "US-FED", "HARD", None, None, None, None, None, None, "role_eligibility", None,
                        None, "Barista", "ROLE", None, False, False, 0, date(2020, 1, 1), None, "VERIFIED")
    excluded_metric = Rule("WEEK", "US-FED", "HARD", None, None, None, None, None, None, "hours_per_week", "<=",
                           Decimal("40"), None, "HOURS", None, False, False, 0, date(2020, 1, 1), None, "VERIFIED")
    waiver = Waiver(f"WV-{employee_id}", employee_id, "ROLE", date(2020, 1, 1), None, "fixture")
    return Snapshot(TENANT, "America/New_York", ("US-FED",), {"fww_covered": False},
                    Employee(employee_id, date(1990, 1, 1), Decimal("16"), False, None, True),
                    (allowed_role, excluded_metric), (waiver,), {DAY: DayFacts(False, False)}, {}, None)


class SnapshotReuseTests(unittest.TestCase):
    # spec: F11.ac22
    def test_ac22_reuses_one_snapshot_per_employee_without_changing_verdicts(self):
        calls: list[tuple[int, str | None]] = []

        def loader(proposal, *, dsn=None):
            calls.append((proposal.employee_id, dsn))
            return _snapshot(proposal.employee_id)

        with patch.object(constraints, "load_snapshot", side_effect=loader):
            adapter = constraints.legality_from_compliance("dsn-a", _inputs())
            first = adapter(_employee(1), ShiftSlot(3, DAY, Decimal("8"), Decimal("10")))
            second = adapter(_employee(1), ShiftSlot(5, DAY + timedelta(days=1), Decimal("10"), Decimal("12")))
            other = adapter(_employee(2), ShiftSlot(3, DAY, Decimal("8"), Decimal("10")))
            self.assertEqual(calls, [(1, "dsn-a"), (2, "dsn-a")])
            uncached_first = constraints.legality_from_compliance("dsn-a", _inputs())(
                _employee(1), ShiftSlot(3, DAY, Decimal("8"), Decimal("10"))
            )
            uncached_second = constraints.legality_from_compliance("dsn-a", _inputs())(
                _employee(1), ShiftSlot(5, DAY + timedelta(days=1), Decimal("10"), Decimal("12"))
            )
        self.assertEqual(first, second)
        self.assertEqual(first, uncached_first)
        self.assertEqual(second, uncached_second)
        self.assertTrue(other.allowed)

    # spec: F11.ac22
    def test_ac22_adapters_for_distinct_dsns_do_not_share_their_closures(self):
        calls: list[str | None] = []

        def loader(proposal, *, dsn=None):
            calls.append(dsn)
            return _snapshot(proposal.employee_id)

        with patch.object(constraints, "load_snapshot", side_effect=loader):
            first = constraints.legality_from_compliance("dsn-a", _inputs())
            second = constraints.legality_from_compliance("dsn-b", _inputs())
            self.assertTrue(first(_employee(1), ShiftSlot(3, DAY, Decimal("8"), Decimal("10"))).allowed)
            self.assertTrue(second(_employee(1), ShiftSlot(3, DAY, Decimal("8"), Decimal("10"))).allowed)
        self.assertEqual(calls, ["dsn-a", "dsn-b"])

    # spec: F11.ac22
    def test_ac22_tenant_and_employer_remain_part_of_the_cache_key(self):
        calls: list[tuple[str, str | None]] = []

        def proposal_with_scope(*args, **kwargs):
            proposal = Proposal(*args, **kwargs)
            proposal.tenant_id = "tenant-a" if proposal.location_id == 3 else "tenant-b"
            proposal.employer_id = "employer-a" if proposal.location_id == 3 else "employer-b"
            return proposal

        def loader(proposal, *, dsn=None):
            calls.append((proposal.tenant_id, proposal.employer_id))
            return _snapshot(proposal.employee_id)

        with patch.object(constraints, "Proposal", side_effect=proposal_with_scope), \
                patch.object(constraints, "load_snapshot", side_effect=loader):
            adapter = constraints.legality_from_compliance("dsn-a", _inputs())
            adapter(_employee(1), ShiftSlot(3, DAY, Decimal("8"), Decimal("10")))
            adapter(_employee(1), ShiftSlot(5, DAY, Decimal("8"), Decimal("10")))
        self.assertEqual(calls, [("tenant-a", "employer-a"), ("tenant-b", "employer-b")])


DSN = os.environ.get("DATABASE_URL", "postgresql://coffeeshop:coffeeshop@localhost:5432/coffeeshop")
_SKIP = None
try:
    import psycopg

    with psycopg.connect(DSN, connect_timeout=3) as connection:
        connection.execute("SELECT 1")
except Exception as exc:  # pragma: no cover
    _SKIP = f"psycopg or database unavailable: {exc}"


class _Rollback(Exception):
    pass


@unittest.skipIf(_SKIP, _SKIP or "")
class SnapshotReuseDatabaseTests(unittest.TestCase):
    # spec: F11.ac22
    def test_ac22_snapshot_is_span_independent_and_adapter_loads_once_per_employee(self):
        from agents.demand_forecast.feature_build import build_feature_set
        from agents.demand_forecast.inference import build_inference_rows
        from agents.demand_forecast.model import persist_forecasts, predict, train
        from agents.scheduler.inputs import assemble_solver_inputs
        from data_access.session import open_session_context

        version = "test-bug073"
        target = (DAY, DAY)
        try:
            with psycopg.connect(DSN) as conn, conn.transaction():
                open_session_context(conn, role="app_rw", tenant_id=TENANT)
                build_feature_set(conn, feature_version=version)
                train(conn, model_version=version, feature_version=version, window=(date(2023, 1, 1), date(2023, 6, 2)))
                rows = build_inference_rows(conn, window=target, history_feature_version=version)
                persist_forecasts(conn, predict(conn, version, target_window=target, rows=rows))
                inputs = assemble_solver_inputs(conn, target_window=target, model_version=version)
                employee = inputs.employees[0]
                start = datetime(2026, 12, 7, 8, tzinfo=ZoneInfo("America/New_York"))
                first = Proposal(TENANT, 3, employee.employee_id, employee.role, start, start + timedelta(hours=2), [])
                second = Proposal(TENANT, 5, employee.employee_id, employee.role, start + timedelta(days=1),
                                  start + timedelta(days=1, hours=2), [])
                self.assertEqual(load_snapshot(first, dsn=DSN), load_snapshot(second, dsn=DSN))
                calls = 0
                original = constraints.load_snapshot

                def counted(proposal, *, dsn=None):
                    nonlocal calls
                    calls += 1
                    return original(proposal, dsn=dsn)

                with patch.object(constraints, "load_snapshot", side_effect=counted):
                    adapter = constraints.legality_from_compliance(DSN, inputs)
                    before = adapter(employee, ShiftSlot(3, DAY, Decimal("8"), Decimal("10")))
                    after = adapter(employee, ShiftSlot(5, DAY, Decimal("10"), Decimal("12")))
                self.assertEqual(calls, 1)
                self.assertEqual(before, after)
                raise _Rollback()
        except _Rollback:
            pass


if __name__ == "__main__":
    unittest.main()
