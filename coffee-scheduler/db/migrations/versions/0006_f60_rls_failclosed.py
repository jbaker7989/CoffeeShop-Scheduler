"""F60-3 — make tenant RLS fail closed on malformed session context.

Revision ID: 0006
Revises: 0005
"""
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

TENANT_TABLES = [
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
    "employee_pii",
]

_INLINE_TENANT_POLICY = (
    "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"
)
_INLINE_TENANT_SELF_POLICY = (
    "id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"
)


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app_current_tenant() RETURNS uuid
        LANGUAGE plpgsql STABLE AS $$
        BEGIN
          RETURN NULLIF(current_setting('app.tenant_id', true), '')::uuid;
        EXCEPTION WHEN invalid_text_representation THEN
          RETURN NULL;
        END; $$;
        """
    )
    for t in TENANT_TABLES:
        op.execute(f'DROP POLICY IF EXISTS tenant_isolation ON "{t}"')
        op.execute(
            f'CREATE POLICY tenant_isolation ON "{t}" '
            "USING (tenant_id = app_current_tenant()) "
            "WITH CHECK (tenant_id = app_current_tenant())"
        )
    op.execute("DROP POLICY IF EXISTS tenant_self_isolation ON tenants")
    op.execute(
        "CREATE POLICY tenant_self_isolation ON tenants "
        "USING (id = app_current_tenant()) "
        "WITH CHECK (id = app_current_tenant())"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_self_isolation ON tenants")
    op.execute(
        "CREATE POLICY tenant_self_isolation ON tenants "
        f"USING ({_INLINE_TENANT_SELF_POLICY}) "
        f"WITH CHECK ({_INLINE_TENANT_SELF_POLICY})"
    )
    for t in TENANT_TABLES:
        op.execute(f'DROP POLICY IF EXISTS tenant_isolation ON "{t}"')
        op.execute(
            f'CREATE POLICY tenant_isolation ON "{t}" '
            f"USING ({_INLINE_TENANT_POLICY}) "
            f"WITH CHECK ({_INLINE_TENANT_POLICY})"
        )
    op.execute("DROP FUNCTION IF EXISTS app_current_tenant()")
