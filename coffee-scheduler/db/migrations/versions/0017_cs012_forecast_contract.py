"""CS-012: harden the forecasts table to the F8 output contract."""
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


UP_SQL = """
ALTER TABLE forecasts
  ALTER COLUMN location_id SET NOT NULL,
  ALTER COLUMN product_category SET NOT NULL,
  ALTER COLUMN forecast_hour SET NOT NULL,
  ALTER COLUMN p70 SET NOT NULL,
  ALTER COLUMN lower SET NOT NULL,
  ALTER COLUMN upper SET NOT NULL,
  ALTER COLUMN model_version SET NOT NULL,
  ALTER COLUMN taxonomy_version SET NOT NULL;

ALTER TABLE forecasts
  ADD CONSTRAINT forecasts_interval_valid
  CHECK (lower >= 0 AND lower <= p70 AND p70 <= upper);

ALTER TABLE forecasts
  ADD CONSTRAINT forecasts_taxonomy_version_fkey
  FOREIGN KEY (tenant_id, taxonomy_version)
  REFERENCES taxonomy_meta (tenant_id, taxonomy_version);

ALTER TABLE forecasts
  ADD CONSTRAINT forecasts_product_category_fkey
  FOREIGN KEY (tenant_id, product_category)
  REFERENCES throughput_standards (tenant_id, product_category);

CREATE UNIQUE INDEX forecasts_live_key_uniq
  ON forecasts (tenant_id, location_id, product_category, forecast_hour, model_version)
  WHERE deleted_at IS NULL;
"""

DOWN_SQL = """
DROP INDEX IF EXISTS forecasts_live_key_uniq;
ALTER TABLE forecasts DROP CONSTRAINT IF EXISTS forecasts_product_category_fkey;
ALTER TABLE forecasts DROP CONSTRAINT IF EXISTS forecasts_taxonomy_version_fkey;
ALTER TABLE forecasts DROP CONSTRAINT IF EXISTS forecasts_interval_valid;
ALTER TABLE forecasts
  ALTER COLUMN location_id DROP NOT NULL,
  ALTER COLUMN product_category DROP NOT NULL,
  ALTER COLUMN forecast_hour DROP NOT NULL,
  ALTER COLUMN p70 DROP NOT NULL,
  ALTER COLUMN lower DROP NOT NULL,
  ALTER COLUMN upper DROP NOT NULL,
  ALTER COLUMN model_version DROP NOT NULL,
  ALTER COLUMN taxonomy_version DROP NOT NULL;
"""


def upgrade() -> None:
    op.execute(UP_SQL)


def downgrade() -> None:
    op.execute(DOWN_SQL)
