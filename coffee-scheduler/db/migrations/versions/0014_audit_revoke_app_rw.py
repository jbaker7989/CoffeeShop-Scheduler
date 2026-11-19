"""Audit hardening (1/2): revoke app_rw's direct audit_log grants.

Reconstructed to match the shared data plane (DB was at revision 0015 while the repo chain
ended at 0013; the original 0014/0015 files were never committed). CS-061c (0012) granted
``app_rw`` ``INSERT, SELECT`` on ``audit_log`` (the architecture §14.3 "app_rw INSERT only"
model). This revision removes that direct grant so audit rows are written **only** through the
``auth_svc`` SECURITY DEFINER function ``auth_record_audit`` — the path the gateway already uses.
Paired with 0015 (the append-only trigger) this makes audit_log function-write-only and immutable.
"""
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        REVOKE INSERT, SELECT ON audit_log FROM app_rw;
        REVOKE USAGE, SELECT ON SEQUENCE audit_log_id_seq FROM app_rw;
        """
    )


def downgrade() -> None:
    # Restore the CS-061c (0012) grant model: app_rw may INSERT + SELECT audit rows directly.
    op.execute(
        """
        GRANT INSERT, SELECT ON audit_log TO app_rw;
        GRANT USAGE, SELECT ON SEQUENCE audit_log_id_seq TO app_rw;
        """
    )
