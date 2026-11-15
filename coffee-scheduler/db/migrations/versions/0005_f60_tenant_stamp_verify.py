"""F60-2 — verify tenant stamping and guard migration idempotency.

Revision ID: 0005
Revises: 0004
"""
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

MAVEN = "00000000-0000-0000-0000-000000000001"

DOMAIN_TABLES = [
    "taxonomy_meta",
    "stores",
    "store_hours",
    "school_calendars",
    "school_calendar_days",
    "holidays",
    "employer_config",
    "stations",
    "fixed_tasks",
    "coverage_rules",
    "shift_rules",
    "cost_targets",
    "throughput_standards",
    "demand_adjustments",
    "product_taxonomy",
    "calibration_log",
    "employees",
    "employee_pii",
    "labor_rules",
    "consent_waivers",
    "events",
    "schedules",
    "shifts",
    "shift_assignments",
    "forecasts",
    "rule_eval_log",
    "transactions",
    "weather_hourly",
    "weather_daily",
]


def upgrade() -> None:
    conn = op.get_bind()
    for t in DOMAIN_TABLES:
        before = conn.exec_driver_sql(f'SELECT count(*) FROM "{t}"').scalar()
        # Defensive, idempotent no-op on the current NOT NULL schema:
        conn.exec_driver_sql(
            f'UPDATE "{t}" SET tenant_id = %s WHERE tenant_id IS NULL',
            (MAVEN,),
        )
        after = conn.exec_driver_sql(f'SELECT count(*) FROM "{t}"').scalar()
        if before != after:
            raise RuntimeError(
                f"{t}: row count changed during verification ({before} -> {after})"
            )
        nulls = conn.exec_driver_sql(
            f'SELECT count(*) FROM "{t}" WHERE tenant_id IS NULL'
        ).scalar()
        if nulls:
            raise RuntimeError(f"{t}: {nulls} rows have NULL tenant_id after backfill")


def downgrade() -> None:
    # Verification/no-op migration — nothing to undo. Reversible by construction.
    pass
