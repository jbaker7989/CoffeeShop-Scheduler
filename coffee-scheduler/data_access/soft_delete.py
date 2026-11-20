"""Soft-delete access helpers for editable tenant-domain records.

Repositories never emit ``DELETE`` on a domain table; callers use
``soft_delete`` instead.  These helpers require an already-open transaction with
``open_session_context`` applied, so RLS and the caller's tenant boundary remain
the authority.  Predicate values must be supplied as a ``(sql, params)`` pair;
the string-only form is only for a value-free, trusted SQL predicate.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


# This is the one policy catalog used by the migration, helpers, guard, and tests.
TIER_A = [
    "employer_config", "stores", "store_hours", "stations", "fixed_tasks",
    "coverage_rules", "shift_rules", "cost_targets", "throughput_standards",
    "demand_adjustments", "product_taxonomy", "taxonomy_meta", "employees",
    "employee_pii", "labor_rules", "consent_waivers", "school_calendars",
    "school_calendar_days", "holidays", "schedules", "shifts",
    "shift_assignments", "forecasts",
]
TIER_B = ["tenants", "tenant_invitations", "tenant_users", "events"]
TIER_C = [
    "calibration_log", "rule_eval_log", "audit_log", "auth_login_attempts",
    "transactions", "weather_hourly", "weather_daily", "weather_codes",
    "auth_refresh_tokens", "auth_revoked_tokens", "auth_password_reset_tokens",
    "auth_mfa_recovery_codes", "onboarding_verification_tokens", "saas_admins",
]
TIER_A_SET = frozenset(TIER_A)


class SoftDeleteError(ValueError):
    """Raised before SQL when a caller asks to soft-delete an excluded relation."""


def _table(table: str) -> str:
    if table not in TIER_A_SET:
        raise SoftDeleteError(f"{table!r} is not a soft-deletable Tier-A table")
    # TIER_A is code-owned and fixed; retain identifier quoting as a defense-in-depth
    # boundary should the catalog ever be edited incorrectly.
    return '"' + table.replace('"', '""') + '"'


def _predicate(where: str | tuple[str, Sequence[Any] | Mapping[str, Any]]) -> tuple[str, Sequence[Any] | Mapping[str, Any]]:
    if isinstance(where, tuple) and len(where) == 2 and isinstance(where[0], str):
        return where
    if isinstance(where, str):
        if "%" in where:
            raise SoftDeleteError("parameterized where clauses must be (sql, params) pairs")
        return where, ()
    raise SoftDeleteError("where must be a trusted SQL string or a (sql, params) pair")


def _params(prefix: tuple[Any, ...], predicate_params: Sequence[Any] | Mapping[str, Any]):
    if isinstance(predicate_params, Mapping):
        raise SoftDeleteError("where parameters must use positional placeholders")
    return (*prefix, *predicate_params)


def enable_show_deleted(conn) -> None:
    """Temporarily let the current transaction read rows hidden by the RLS policy."""
    conn.execute("SET LOCAL app.show_deleted = 'on'")


def _show_deleted_for_write(conn):
    """Temporarily bypass the SELECT policy's UPDATE visibility/WCO interaction."""
    previous = conn.execute("SELECT current_setting('app.show_deleted', true)").fetchone()[0]
    enable_show_deleted(conn)
    return previous


def _restore_show_deleted_setting(conn, previous) -> None:
    if previous is None:
        conn.execute("SET LOCAL app.show_deleted = DEFAULT")
    else:
        conn.execute("SELECT set_config('app.show_deleted', %s, true)", (previous,))


def soft_delete(conn, table: str, *, tenant_id, where, actor_id) -> int:
    """Mark matching live rows deleted and return the UPDATE rowcount.

    ``RETURNING`` is deliberately absent: the restrictive SELECT policy hides a row
    immediately after its marker is written, so returning the row is unreliable.
    """
    predicate, predicate_params = _predicate(where)
    previous = _show_deleted_for_write(conn)
    try:
        result = conn.execute(
            f"UPDATE {_table(table)} SET deleted_at = now(), deleted_by = %s "
            f"WHERE tenant_id = %s AND ({predicate}) AND deleted_at IS NULL",
            _params((actor_id, tenant_id), predicate_params),
        )
        return result.rowcount
    finally:
        _restore_show_deleted_setting(conn, previous)


def restore(conn, table: str, *, tenant_id, where) -> int:
    """Restore matching deleted rows, enabling their transaction-local admin view first."""
    predicate, predicate_params = _predicate(where)
    enable_show_deleted(conn)
    result = conn.execute(
        f"UPDATE {_table(table)} SET deleted_at = NULL, deleted_by = NULL "
        f"WHERE tenant_id = %s AND ({predicate}) AND deleted_at IS NOT NULL",
        _params((tenant_id,), predicate_params),
    )
    return result.rowcount


def list_including_deleted(conn, table: str, *, tenant_id, where="TRUE") -> list[tuple]:
    """Read a Tier-A table including deleted rows within the current transaction."""
    predicate, predicate_params = _predicate(where)
    enable_show_deleted(conn)
    return conn.execute(
        f"SELECT * FROM {_table(table)} WHERE tenant_id = %s AND ({predicate})",
        _params((tenant_id,), predicate_params),
    ).fetchall()
