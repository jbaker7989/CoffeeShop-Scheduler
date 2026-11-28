"""BUG-067: validate the finite interval check without an ACCESS EXCLUSIVE scan."""
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None

CONSTRAINT = "forecasts_interval_finite"

PREFLIGHT_SQL = """
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM forecasts
    WHERE lower IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)
       OR p70   IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)
       OR upper IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)
  ) THEN
    RAISE EXCEPTION
      'BUG-065 preflight: forecasts has non-finite lower/p70/upper values; '
      'clean these rows before retrying migration 0024 (constraint forecasts_interval_finite)'
      USING ERRCODE = 'check_violation';
  END IF;
END $$;
"""

# VALIDATE CONSTRAINT scans historical rows under SHARE UPDATE EXCLUSIVE, which
# remains compatible with normal SELECT/INSERT/UPDATE/DELETE traffic. Bound only
# the short lock acquisition; do not hold ACCESS EXCLUSIVE through the scan.
LOCK_TIMEOUT_SQL = "SET LOCAL lock_timeout = '5s';"
VALIDATE_SQL = f"ALTER TABLE forecasts VALIDATE CONSTRAINT {CONSTRAINT};"
DROP_SQL = f"ALTER TABLE forecasts DROP CONSTRAINT {CONSTRAINT};"
REINSTALL_SQL = f"""
ALTER TABLE forecasts ADD CONSTRAINT {CONSTRAINT}
  CHECK (
    lower <> 'NaN'::numeric AND lower <> 'Infinity'::numeric AND lower <> '-Infinity'::numeric
    AND p70 <> 'NaN'::numeric AND p70 <> 'Infinity'::numeric AND p70 <> '-Infinity'::numeric
    AND upper <> 'NaN'::numeric AND upper <> 'Infinity'::numeric AND upper <> '-Infinity'::numeric
  ) NOT VALID;
"""


def upgrade() -> None:
    # Alembic normally wraps an entire `upgrade head` in one transaction. Commit
    # the 0021 enforcement boundary before scanning legacy rows, so a preflight or
    # validation failure leaves that protection installed and retryable.
    with op.get_context().autocommit_block():
        pass
    op.execute(LOCK_TIMEOUT_SQL)
    op.execute(PREFLIGHT_SQL)
    op.execute(VALIDATE_SQL)


def downgrade() -> None:
    # PostgreSQL has no UNVALIDATE operation. Recreate the same enforcement
    # state that revision 0021 introduced, atomically within this transaction.
    op.execute(LOCK_TIMEOUT_SQL)
    op.execute(DROP_SQL)
    op.execute(REINSTALL_SQL)
