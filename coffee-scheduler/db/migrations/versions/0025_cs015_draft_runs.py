"""RLS-scoped durable workflow runs and attempt fencing for CS-015.

Revision ID: 0025
Revises: 0024
"""
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE schedule_draft_runs (
            tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            id uuid NOT NULL,
            issuer text NOT NULL,
            subject text NOT NULL,
            request_hash text NOT NULL,
            attempt uuid,
            lease_until timestamptz NOT NULL,
            stage text NOT NULL CHECK (stage IN ('start', 'gather', 'forecast', 'done')),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY (tenant_id, id)
        )
    """)
    op.execute("ALTER TABLE schedule_draft_runs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE schedule_draft_runs FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation ON schedule_draft_runs
            USING (tenant_id = app_current_tenant())
            WITH CHECK (tenant_id = app_current_tenant())
    """)
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON schedule_draft_runs TO app_rw")
    op.execute("""
        CREATE INDEX ix_schedule_draft_runs_active ON schedule_draft_runs (tenant_id, lease_until)
        WHERE attempt IS NOT NULL
    """)


def downgrade():
    op.execute("""
        DELETE FROM langgraph_checkpoints AS c USING schedule_draft_runs AS r
        WHERE c.tenant_id = r.tenant_id AND c.agent_id = r.id
          AND c.checkpoint->>'workflow' = 'schedule-draft'
    """)
    op.execute("DROP TABLE schedule_draft_runs")
