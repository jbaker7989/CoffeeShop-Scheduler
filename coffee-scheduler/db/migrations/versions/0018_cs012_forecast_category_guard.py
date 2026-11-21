"""CS-012/BUG-060: exclude the register-overhead pseudo-category from forecasts."""
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


# `(per transaction overhead)` is the fixed sentinel row carried in throughput_standards.
UP_SQL = """
ALTER TABLE forecasts
  ADD CONSTRAINT forecasts_category_is_product
  CHECK (product_category <> '(per transaction overhead)');
"""

DOWN_SQL = """
ALTER TABLE forecasts DROP CONSTRAINT IF EXISTS forecasts_category_is_product;
"""


def upgrade() -> None:
    op.execute(UP_SQL)


def downgrade() -> None:
    op.execute(DOWN_SQL)
