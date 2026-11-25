"""BUG-065 / CS-012: reject non-finite forecast interval values in PostgreSQL.

Migration ``0017`` constrains intervals with only ``lower >= 0 AND lower <= p70
AND p70 <= upper``. PostgreSQL ``numeric`` accepts the special values ``NaN``,
``Infinity`` and ``-Infinity``, and its ordering treats ``NaN`` as equal to
itself and greater than every ordinary number, so tuples such as
``(NaN, NaN, NaN)``, ``(0, NaN, NaN)`` and ``(0, 1, Infinity)`` satisfy that
ordered CHECK. F8.ac3 requires finite intervals, so add an explicit per-column
finite-value constraint that rejects each special value independently.

``v <> 'NaN'`` is FALSE for a ``NaN`` value (PostgreSQL defines ``NaN = NaN`` as
TRUE), so the exclusion rejects ``NaN`` as well as the two infinities, and leaves
the existing interval rules in force for finite values.

The preflight scans with an ordinary read lock. ADD CONSTRAINT NOT VALID
holds ACCESS EXCLUSIVE only through the catalog update and its commit.
Validation then scans under SHARE UPDATE EXCLUSIVE, which permits ordinary
reads and writes but conflicts with some maintenance and schema changes.

Drain long-running forecast readers before applying 0021. The metadata phase
waits up to five seconds for a conflicting lock. Retry after readers release
their locks. If a legacy non-finite row arrives between preflight and
enforcement, validation raises a constraint error. After a validation failure
the installed constraint still enforces new writes, and a retry runs the
preflight again and reuses the existing constraint.
"""
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None

CONSTRAINT = "forecasts_interval_finite"

LOCK_TIMEOUT_SQL = "SET LOCAL lock_timeout = '5s';"

# Preflight: adding the CHECK would itself fail on pre-existing non-finite rows,
# but with an opaque constraint-violation error. Surface a clear, actionable
# message instead, and never rewrite forecast values.
PREFLIGHT_SQL = f"""
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
      'clean these rows before applying migration 0021 (constraint %)', '{CONSTRAINT}'
      USING ERRCODE = 'check_violation';
  END IF;
END $$;
"""

UP_SQL = f"""
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conrelid = 'forecasts'::regclass AND conname = '{CONSTRAINT}'
  ) THEN
    ALTER TABLE forecasts
      ADD CONSTRAINT {CONSTRAINT}
      CHECK (
        lower <> 'NaN'::numeric AND lower <> 'Infinity'::numeric AND lower <> '-Infinity'::numeric
        AND p70 <> 'NaN'::numeric AND p70 <> 'Infinity'::numeric AND p70 <> '-Infinity'::numeric
        AND upper <> 'NaN'::numeric AND upper <> 'Infinity'::numeric AND upper <> '-Infinity'::numeric
      ) NOT VALID;
  END IF;
END $$;
"""

VALIDATE_SQL = f"ALTER TABLE forecasts VALIDATE CONSTRAINT {CONSTRAINT};"

# Downgrade removes ONLY this constraint; the 0017 ordering/FK/unique objects stay intact.
DOWN_SQL = f"ALTER TABLE forecasts DROP CONSTRAINT IF EXISTS {CONSTRAINT};"


def upgrade() -> None:
    op.execute(LOCK_TIMEOUT_SQL)
    op.execute(PREFLIGHT_SQL)
    op.execute(UP_SQL)

    # Alembic commits on entry and starts a new transaction on exit. Release
    # ACCESS EXCLUSIVE before validation acquires its weaker lock.
    with op.get_context().autocommit_block():
        pass

    op.execute("SET LOCAL lock_timeout = '0';")
    op.execute(VALIDATE_SQL)


def downgrade() -> None:
    op.execute(DOWN_SQL)
