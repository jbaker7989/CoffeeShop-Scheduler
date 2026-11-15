"""BUG-020 / F60 — RLS on the `tenants` root table (stop cross-tenant enumeration).

The `tenants` registry had no RLS, so any `app_rw`/`app_ro` session could read EVERY
tenant's slug/name/timezone/jurisdiction. This adds a self-scoping policy so a non-owner
app session sees only its own tenant row.

Design nuance: `ENABLE` (not `FORCE`). The table owner / migration / ETL role MUST retain
full visibility to provision tenants and resolve a tenant by slug (the ETL does
`SELECT id FROM tenants WHERE slug = …` with no `app.tenant_id` set) — `FORCE` would make
that return zero rows and break the loader and the seed. Owner bypasses RLS under `ENABLE`;
the non-owner `app_rw`/`app_ro` roles are scoped by the policy. (Domain tables use `FORCE`
because no legitimate app path needs cross-tenant visibility there; the tenant registry is
the one place the admin path does.)

Revision ID: 0004
Revises: 0003
"""
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

SELF = "id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def upgrade() -> None:
    op.execute("ALTER TABLE tenants ENABLE ROW LEVEL SECURITY")
    # Deliberately NOT FORCE — see the module docstring (owner/ETL must see all tenants).
    op.execute(f"CREATE POLICY tenant_self_isolation ON tenants USING ({SELF}) WITH CHECK ({SELF})")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_self_isolation ON tenants")
    op.execute("ALTER TABLE tenants DISABLE ROW LEVEL SECURITY")
