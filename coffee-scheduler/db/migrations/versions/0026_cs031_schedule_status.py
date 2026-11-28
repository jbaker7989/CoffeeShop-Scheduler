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
    # NOT VALID exempts rows that already exist; it still enforces every new insert and
    # update, so 'legacy-import' (written by scripts/etl_excel_to_postgres.py) must be listed.
    op.create_check_constraint(
        "schedules_status_check",
        "schedules",
        "status IN ('draft', 'published', 'superseded', 'legacy-import')",
        postgresql_not_valid=True,
    )


def downgrade() -> None:
    op.drop_constraint("schedules_status_check", "schedules", type_="check")
