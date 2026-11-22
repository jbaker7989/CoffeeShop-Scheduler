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
every finite value — including ``0`` and negative decimals — accepted.
"""
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None

CONSTRAINT = "forecasts_interval_finite"

# Serialize against concurrent writers. `ADD CONSTRAINT` already takes ACCESS
# EXCLUSIVE, but taking it up front — inside Alembic's migration transaction, so
# it is held until commit — closes the window where a writer could insert a
# non-finite row *after* the preflight SELECT but *before* the ALTER, which would
# otherwise trip the opaque constraint error instead of the actionable preflight
# message.
#
# OPERATIONAL IMPACT (assess before deploying): the ACCESS EXCLUSIVE lock blocks
# ALL reads and writes to `forecasts` for the whole migration transaction, and
# `ADD CONSTRAINT` scans every row to validate it. Both scale with production
# `forecasts` volume, which is not characterized here, so size the table and
# schedule a maintenance window accordingly. The fixed `lock_timeout` below makes
# lock acquisition fail fast — rather than queue indefinitely behind a
# long-running reader — so a blocked migration aborts cleanly; if acquisition
# times out, retry the migration in a quieter window. Each statement runs in its
# own op.execute so neither depends on multi-command execution support.
LOCK_TIMEOUT_SQL = "SET LOCAL lock_timeout = '5s';"
LOCK_SQL = "LOCK TABLE forecasts IN ACCESS EXCLUSIVE MODE;"

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
ALTER TABLE forecasts
  ADD CONSTRAINT {CONSTRAINT}
  CHECK (
    lower <> 'NaN'::numeric AND lower <> 'Infinity'::numeric AND lower <> '-Infinity'::numeric
    AND p70 <> 'NaN'::numeric AND p70 <> 'Infinity'::numeric AND p70 <> '-Infinity'::numeric
    AND upper <> 'NaN'::numeric AND upper <> 'Infinity'::numeric AND upper <> '-Infinity'::numeric
  );
"""

# Downgrade removes ONLY this constraint; the 0017 ordering/FK/unique objects stay intact.
DOWN_SQL = f"ALTER TABLE forecasts DROP CONSTRAINT IF EXISTS {CONSTRAINT};"


def upgrade() -> None:
    op.execute(LOCK_TIMEOUT_SQL)
    op.execute(LOCK_SQL)
    op.execute(PREFLIGHT_SQL)
    op.execute(UP_SQL)


def downgrade() -> None:
    op.execute(DOWN_SQL)
