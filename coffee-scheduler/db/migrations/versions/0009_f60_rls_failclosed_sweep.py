"""F60-6 — sweep tenant RLS policies to the fail-closed helper.

Revision ID: 0009
Revises: 0008
"""
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.exec_driver_sql(
        """
        SELECT c.relname
        FROM pg_policy p
        JOIN pg_class c ON c.oid = p.polrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE p.polname = 'tenant_isolation'
          AND n.nspname = 'public'
          AND pg_get_expr(p.polqual, p.polrelid) NOT LIKE '%%app_current_tenant%%'
        """
    )
    for (relname,) in rows:
        op.execute(f'DROP POLICY IF EXISTS tenant_isolation ON "{relname}"')
        op.execute(
            f'CREATE POLICY tenant_isolation ON "{relname}" '
            "USING (tenant_id = app_current_tenant()) "
            "WITH CHECK (tenant_id = app_current_tenant())"
        )


def downgrade() -> None:
    inline = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"
    op.execute('DROP POLICY IF EXISTS tenant_isolation ON "langgraph_checkpoints"')
    op.execute(
        f'CREATE POLICY tenant_isolation ON "langgraph_checkpoints" '
        f"USING ({inline}) WITH CHECK ({inline})"
    )
