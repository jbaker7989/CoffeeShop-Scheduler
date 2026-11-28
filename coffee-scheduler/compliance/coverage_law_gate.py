"""Run the standalone CS-022 check on migrated and seeded PostgreSQL data."""
from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from datetime import date
from typing import TYPE_CHECKING, TextIO
from uuid import UUID

from .coverage_law import CoverageRule, DateRange, LaborRule, reconcile

if TYPE_CHECKING:
    from psycopg.types.range import Range


class GateSetupError(RuntimeError):
    pass


def _date_range(value: Range[date]) -> DateRange:
    return DateRange(value.lower, value.upper, value.isempty)


def _require_seed(coverage: Sequence[CoverageRule], labor: Sequence[LaborRule]) -> None:
    if not coverage or not labor:
        raise GateSetupError(f"missing live seed rows (coverage={len(coverage)} labor={len(labor)})")


def load_tenant_rules(conn, tenant_id: str, tenant_slug: str) -> tuple[list[CoverageRule], list[LaborRule]]:
    """Read a seed tenant inside an already-scoped application read transaction."""
    role, tenant, tenant_rls, coverage_rls, labor_rls = conn.execute(
        """SELECT current_user, current_setting('app.tenant_id', true), row_security_active('tenants'),
                  row_security_active('coverage_rules'), row_security_active('labor_rules')"""
    ).fetchone()
    if role != "app_ro" or tenant != tenant_id or not (tenant_rls and coverage_rls and labor_rls):
        raise GateSetupError("expected app_ro tenant context with active RLS")
    if conn.execute("SELECT slug FROM tenants WHERE id = %s", (tenant_id,)).fetchone() != (tenant_slug,):
        raise GateSetupError("visible seed tenant identity does not match the requested tenant ID and slug")

    coverage = [
        CoverageRule(str(owner), rule_id, description, value, basis,
                     _date_range(validity) if validity is not None else None)
        for owner, rule_id, description, value, basis, validity in conn.execute(
            """SELECT tenant_id, rule_id, description, value, basis, validity
               FROM coverage_rules WHERE tenant_id = %s AND deleted_at IS NULL""", (tenant_id,),
        )
    ]
    labor = [
        LaborRule(str(owner), rule_id, _date_range(validity), enforcement, status, verified_on, citation, url)
        for owner, rule_id, validity, enforcement, status, verified_on, citation, url in conn.execute(
            """SELECT tenant_id, rule_id, validity, enforcement, verification_status, verified_on,
                      source_citation, source_url
               FROM labor_rules WHERE tenant_id = %s AND deleted_at IS NULL""", (tenant_id,),
        )
    ]
    _require_seed(coverage, labor)
    return coverage, labor


def _interval_label(interval: DateRange | None) -> str:
    if interval is None:
        return "NULL"
    if interval.empty:
        return "empty"
    return f"{'[' if interval.lower else '('}{interval.lower or ''},{interval.upper or ''})"


def run_gate(coverage: Sequence[CoverageRule], labor: Sequence[LaborRule], *, output: TextIO) -> int:
    try:
        _require_seed(coverage, labor)
    except GateSetupError as exc:
        print(f"SETUP ERROR {exc}", file=output)
        return 2
    findings = reconcile(coverage, labor)
    for item in findings:
        print(
            f"FAIL tenant={item.tenant_id} coverage={item.coverage_rule_id} labor={item.labor_rule_id or '-'} "
            f"interval={_interval_label(item.interval)} condition={item.condition}", file=output,
        )
    if findings:
        return 1
    print(f"PASS checked coverage={len(coverage)} labor={len(labor)}", file=output)
    return 0


def _load_database(dsn: str, tenant_id: str, tenant_slug: str) -> tuple[list[CoverageRule], list[LaborRule]]:
    try:
        from psycopg import Error

        from data_access.session import SessionContextError, tenant_session
    except ImportError:
        raise GateSetupError("database driver unavailable (install psycopg)") from None

    try:
        with tenant_session(dsn, role="app_ro", tenant_id=tenant_id, repeatable_read=True) as conn:
            return load_tenant_rules(conn, tenant_id, tenant_slug)
    except (Error, SessionContextError):
        # Database exceptions can include connection settings or raw rule values.
        raise GateSetupError("database read failed (check connectivity, migrations, and app_ro access)") from None


def main(argv: Sequence[str] | None = None, *, output: TextIO | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", type=UUID, required=True)
    parser.add_argument("--tenant-slug", required=True)
    args = parser.parse_args(argv)
    output = output if output is not None else sys.stdout
    try:
        dsn = os.environ.get("DATABASE_URL")
        if not dsn or not dsn.strip():
            raise GateSetupError("DATABASE_URL is required")
        coverage, labor = _load_database(dsn, str(args.tenant_id), args.tenant_slug)
    except GateSetupError as exc:
        print(f"SETUP ERROR {exc}", file=output)
        return 2
    return run_gate(coverage, labor, output=output)


if __name__ == "__main__":
    sys.exit(main())
