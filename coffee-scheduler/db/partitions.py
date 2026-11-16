"""Helpers that keep dynamically created partitions protected by tenant RLS."""


def ensure_partition_rls(conn, partition_name: str) -> None:
    """Apply ENABLE + FORCE RLS and the tenant_isolation policy (fail-closed app_current_tenant())
    to a newly-created partition. The ops partition-creation path MUST call this for every new
    partition so no partition ever ships without RLS (BUG-021). `partition_name` must be a trusted,
    validated identifier (a partition name the caller generated), never raw user input — it is
    double-quoted but not otherwise sanitised.
    """
    partition = f'"{partition_name}"'
    conn.execute(f"ALTER TABLE {partition} ENABLE ROW LEVEL SECURITY;")
    conn.execute(f"ALTER TABLE {partition} FORCE ROW LEVEL SECURITY;")
    conn.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {partition};")
    conn.execute(
        f"CREATE POLICY tenant_isolation ON {partition} "
        "USING (tenant_id = app_current_tenant()) "
        "WITH CHECK (tenant_id = app_current_tenant());"
    )
