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

# Install enforcement without scanning historical rows. PostgreSQL still needs a
# brief ACCESS EXCLUSIVE metadata lock; the timeout bounds deployment contention.
# A subsequent migration performs the preflight and online validation after this
# constraint is already preventing new invalid values.
LOCK_TIMEOUT_SQL = "SET LOCAL lock_timeout = '5s';"

UP_SQL = f"""
ALTER TABLE forecasts
  ADD CONSTRAINT {CONSTRAINT}
  CHECK (
    lower <> 'NaN'::numeric AND lower <> 'Infinity'::numeric AND lower <> '-Infinity'::numeric
    AND p70 <> 'NaN'::numeric AND p70 <> 'Infinity'::numeric AND p70 <> '-Infinity'::numeric
    AND upper <> 'NaN'::numeric AND upper <> 'Infinity'::numeric AND upper <> '-Infinity'::numeric
  ) NOT VALID;
"""

# Downgrade removes ONLY this constraint; the 0017 ordering/FK/unique objects stay intact.
DOWN_SQL = f"ALTER TABLE forecasts DROP CONSTRAINT IF EXISTS {CONSTRAINT};"


def upgrade() -> None:
    op.execute(LOCK_TIMEOUT_SQL)
    op.execute(UP_SQL)


def downgrade() -> None:
    op.execute(DOWN_SQL)
