"""Constrain the schedule status vocabulary for CS-031.

Revision ID: 0026
Revises: 0025
"""
from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Preserve legacy imports. PostgreSQL enforces this check on all inserts and updates.
    op.create_check_constraint(
        "schedules_status_check", "schedules", "status IN ('draft', 'published', 'superseded')", postgresql_not_valid=True
    )


def downgrade() -> None:
    op.drop_constraint("schedules_status_check", "schedules", type_="check")
