"""PostgreSQL DDL for staged tenant migrations on partitioned tables."""

from uuid import UUID

from psycopg import Connection, sql

QualifiedName = tuple[str, str]


def create_parent_index(
    conn: Connection, table: QualifiedName, index: str, columns: tuple[str, ...],
) -> None:
    """Create the partitioned index metadata without building indexes on the leaves."""
    conn.execute(sql.SQL("CREATE INDEX {} ON ONLY {} ({})").format(
        sql.Identifier(index),
        sql.Identifier(*table),
        sql.SQL(", ").join(map(sql.Identifier, columns)),
    ))


def create_leaf_index_concurrently(
    conn: Connection, table: QualifiedName, index: str, columns: tuple[str, ...],
) -> None:
    """Build a matching index on a leaf using a connection outside a transaction."""
    conn.execute(sql.SQL("CREATE INDEX CONCURRENTLY {} ON {} ({})").format(
        sql.Identifier(index),
        sql.Identifier(*table),
        sql.SQL(", ").join(map(sql.Identifier, columns)),
    ))


def attach_partition_index(conn: Connection, parent: QualifiedName, child: QualifiedName) -> None:
    """Attach one child index. PostgreSQL validates the parent once every child is attached."""
    conn.execute(sql.SQL("ALTER INDEX {} ATTACH PARTITION {}").format(
        sql.Identifier(*parent), sql.Identifier(*child),
    ))


def begin_tenant_backfill(conn: Connection, table: QualifiedName, tenant_id: UUID) -> None:
    """Install the new column, default and write guard atomically before writers resume."""
    parent = sql.Identifier(*table)
    with conn.transaction():
        conn.execute(sql.SQL("ALTER TABLE {} ADD COLUMN tenant_id uuid").format(parent))
        conn.execute(sql.SQL("ALTER TABLE {} ALTER COLUMN tenant_id SET DEFAULT {}").format(
            parent, sql.Literal(tenant_id),
        ))
        conn.execute(sql.SQL("""
            ALTER TABLE {} ADD CONSTRAINT tenant_id_backfill_nn CHECK (tenant_id IS NOT NULL) NOT VALID
        """).format(parent))


def validate_tenant_backfill(conn: Connection, table: QualifiedName) -> None:
    """Validate after the last backfill batch and commit before the final metadata changes."""
    conn.execute(sql.SQL("ALTER TABLE {} VALIDATE CONSTRAINT tenant_id_backfill_nn").format(
        sql.Identifier(*table),
    ))


def finish_tenant_backfill(conn: Connection, table: QualifiedName) -> None:
    """Promote to NOT NULL using the validated check, then remove the temporary guards."""
    parent = sql.Identifier(*table)
    with conn.transaction():
        conn.execute(sql.SQL("ALTER TABLE {} ALTER COLUMN tenant_id SET NOT NULL").format(parent))
        conn.execute(sql.SQL("""
            ALTER TABLE {} ALTER COLUMN tenant_id DROP DEFAULT, DROP CONSTRAINT tenant_id_backfill_nn
        """).format(parent))
