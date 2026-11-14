"""Add effective-dating to coverage rules (F34 / CS-034).

coverage_rules has PRIMARY KEY (tenant_id, rule_id), so it permits only one
version of each rule and an overlapping-window exclusion is unnecessary for now.
"""
from alembic import op


revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE coverage_rules ADD COLUMN validity daterange")
    op.execute(
        "UPDATE coverage_rules "
        "SET validity = daterange(NULL, NULL, '[)') "
        "WHERE validity IS NULL"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE coverage_rules DROP COLUMN validity")
