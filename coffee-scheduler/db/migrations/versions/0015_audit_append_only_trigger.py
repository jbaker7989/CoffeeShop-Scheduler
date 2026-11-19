"""Audit hardening (2/2): enforce audit_log append-only with a trigger.

Reconstructed to match the shared data plane (see 0014). Beyond the grant model, a
``BEFORE UPDATE OR DELETE`` trigger raises so audit rows can never be mutated — even if a future
grant mistake ever handed a role UPDATE/DELETE on ``audit_log``. Immutability no longer depends on
grants alone.
"""
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION audit_reject_mutation() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
        BEGIN RAISE EXCEPTION 'audit_log is append-only'; END $$;
        CREATE TRIGGER audit_log_append_only BEFORE DELETE OR UPDATE ON audit_log
        FOR EACH ROW EXECUTE FUNCTION audit_reject_mutation();
        """
    )


def downgrade() -> None:
    # Drop the trigger first, then the function with CASCADE as a backstop so the function
    # drop never fails on the trigger's dependency (run as separate statements to guarantee
    # ordering through SQLAlchemy/psycopg).
    op.execute("DROP TRIGGER IF EXISTS audit_log_append_only ON audit_log")
    op.execute("DROP FUNCTION IF EXISTS audit_reject_mutation() CASCADE")
