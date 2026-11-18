"""CS-087 soft-delete lifecycle for editable domain records."""
from pathlib import Path
import sys

from alembic import op

# Alembic runs with db/ as its prepended import root; make the scheduler package
# available so this migration and the data-access helpers share one policy catalog.
SCHEDULER_ROOT = Path(__file__).resolve().parents[3]
if str(SCHEDULER_ROOT) not in sys.path:
    sys.path.insert(0, str(SCHEDULER_ROOT))

from data_access.soft_delete import TIER_A  # noqa: E402


revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

SQL = r'''
ALTER TABLE {table} ADD COLUMN deleted_at TIMESTAMPTZ NULL, ADD COLUMN deleted_by UUID NULL;
CREATE INDEX idx_{table}_live ON {table} (tenant_id) WHERE deleted_at IS NULL;
CREATE POLICY soft_delete_hidden ON {table} AS RESTRICTIVE FOR SELECT
  USING (deleted_at IS NULL OR current_setting('app.show_deleted', true) = 'on');
'''

DOWNGRADE_SQL = r'''
DROP POLICY soft_delete_hidden ON {table};
DROP INDEX idx_{table}_live;
ALTER TABLE {table} DROP COLUMN deleted_by, DROP COLUMN deleted_at;
'''


def upgrade() -> None:
    for table in TIER_A:
        # missing_ok=true makes an unset GUC evaluate to NULL, which hides rows.
        op.execute(SQL.format(table=table))


def downgrade() -> None:
    # Reverse per-table object order and reverse table order for a true 0012 boundary.
    for table in reversed(TIER_A):
        op.execute(DOWNGRADE_SQL.format(table=table))
