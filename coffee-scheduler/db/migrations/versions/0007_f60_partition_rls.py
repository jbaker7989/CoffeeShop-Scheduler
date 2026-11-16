"""F60-4 — enforce tenant RLS on every partition of high-volume tables.

Partitioned parents protect reads routed through them, but a direct read of a child
partition has its own RLS settings and policies. Apply fail-closed tenant isolation
to every existing child, including the DEFAULT and any period partitions created by
operations.

Revision ID: 0007
Revises: 0006
"""
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

PARTITION_PARENTS = ["rule_eval_log", "transactions", "weather_hourly"]

_CHILD_PARTITIONS_SQL = """
    SELECT c.relname
    FROM pg_inherits i
    JOIN pg_class c ON c.oid = i.inhrelid
    JOIN pg_class p ON p.oid = i.inhparent
    WHERE p.relname = ANY(%(parents)s)
"""


def _partition_names() -> list[str]:
    conn = op.get_bind()
    rows = conn.exec_driver_sql(_CHILD_PARTITIONS_SQL, {"parents": PARTITION_PARENTS})
    return [row[0] for row in rows]


def upgrade() -> None:
    for partition_name in _partition_names():
        partition = f'"{partition_name}"'
        op.execute(f"ALTER TABLE {partition} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {partition} FORCE ROW LEVEL SECURITY;")
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {partition};")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {partition} "
            "USING (tenant_id = app_current_tenant()) "
            "WITH CHECK (tenant_id = app_current_tenant());"
        )


def downgrade() -> None:
    for partition_name in _partition_names():
        partition = f'"{partition_name}"'
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {partition};")
        op.execute(f"ALTER TABLE {partition} DISABLE ROW LEVEL SECURITY;")
