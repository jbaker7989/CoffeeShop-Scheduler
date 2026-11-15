"""CS-045 / F45 — langgraph_checkpoints table (RLS-protected).

Per-tenant LangGraph checkpoint storage. Because it holds tenant data it is a
tenant-scoped domain table and gets the full F60 treatment: tenant_id NOT NULL,
ENABLE + FORCE RLS, the NULLIF tenant_isolation policy (USING + WITH CHECK), and
app_rw / app_ro grants. Reversible.

Revision ID: 0003
Revises: 0002
"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

POLICY = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE langgraph_checkpoints (
            tenant_id  uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            id         uuid NOT NULL DEFAULT gen_random_uuid(),
            agent_id   uuid NOT NULL,
            checkpoint jsonb NOT NULL,
            -- monotonic ordering: now() is the transaction timestamp (constant within a txn),
            -- so "latest" must order by a per-insert sequence, not created_at.
            seq        bigint GENERATED ALWAYS AS IDENTITY,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY (tenant_id, id)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_lgchk_tenant_agent_seq "
        "ON langgraph_checkpoints (tenant_id, agent_id, seq DESC)"
    )
    op.execute("ALTER TABLE langgraph_checkpoints ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE langgraph_checkpoints FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON langgraph_checkpoints "
        f"USING ({POLICY}) WITH CHECK ({POLICY})"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON langgraph_checkpoints TO app_rw")
    op.execute("GRANT SELECT ON langgraph_checkpoints TO app_ro")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS langgraph_checkpoints CASCADE")
