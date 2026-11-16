"""F60-5 — pin the fail-closed tenant helper's search path.

Revision ID: 0008
Revises: 0007
"""
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app_current_tenant() RETURNS uuid
        LANGUAGE plpgsql STABLE
        SET search_path = pg_catalog
        AS $$
        BEGIN
          RETURN NULLIF(current_setting('app.tenant_id', true), '')::uuid;
        EXCEPTION WHEN invalid_text_representation THEN
          RETURN NULL;
        END; $$;
        """
    )


def downgrade() -> None:
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
