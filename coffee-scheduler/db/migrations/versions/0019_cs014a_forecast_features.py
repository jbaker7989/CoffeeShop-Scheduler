"""CS-014a: versioned forecast feature store (feature_sets + forecast_features).

Tenant-scoped, FORCE-RLS tables that hold the model-ready feature dataset the forecaster
(CS-014b) and backtester (CS-014c) read. Mirrors migration 0001's RLS + grant pattern.
"""
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None

# The fail-closed policy form the isolation gate enforces (migration 0006+): app_current_tenant()
# resolves the request tenant or fails closed, so an unset context matches no rows.
POLICY = "tenant_id = app_current_tenant()"

UP_SQL = """
CREATE TABLE feature_sets (
    tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    feature_version text NOT NULL,
    source_window_start date NOT NULL,
    source_window_end date NOT NULL,
    taxonomy_version text NOT NULL,
    row_count int NOT NULL,
    has_basket_id boolean NOT NULL,
    has_evening_history boolean NOT NULL,
    has_annual_seasonality boolean NOT NULL,
    built_at timestamptz NOT NULL DEFAULT now(),
    notes text,
    PRIMARY KEY (tenant_id, feature_version),
    FOREIGN KEY (tenant_id, taxonomy_version) REFERENCES taxonomy_meta (tenant_id, taxonomy_version),
    CHECK (source_window_start <= source_window_end),
    CHECK (row_count >= 0)
);

CREATE TABLE forecast_features (
    tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    feature_version text NOT NULL,
    location_id int NOT NULL,
    product_category text NOT NULL,
    obs_date date NOT NULL,
    obs_hour int NOT NULL,
    items int NOT NULL,
    revenue numeric(12,2) NOT NULL,
    day_of_week int NOT NULL,
    is_weekend boolean NOT NULL,
    is_holiday boolean NOT NULL,
    school_in_session boolean NOT NULL,
    temperature_2m numeric,
    precipitation numeric,
    rain numeric,
    snowfall numeric,
    weather_bucket text,
    in_trading_hours boolean,
    items_lag_1h int,
    items_roll_7d_same_hour numeric,
    low_history boolean NOT NULL,
    PRIMARY KEY (tenant_id, feature_version, location_id, product_category, obs_date, obs_hour),
    FOREIGN KEY (tenant_id, feature_version)
        REFERENCES feature_sets (tenant_id, feature_version) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, product_category)
        REFERENCES throughput_standards (tenant_id, product_category),
    FOREIGN KEY (tenant_id, location_id) REFERENCES stores (tenant_id, location_id),
    CHECK (obs_hour BETWEEN 0 AND 23),
    CHECK (day_of_week BETWEEN 0 AND 6),
    CHECK (items >= 0)
);

CREATE INDEX forecast_features_series_idx
    ON forecast_features (tenant_id, feature_version, location_id, product_category, obs_date, obs_hour);
"""

RLS_TABLES = ("feature_sets", "forecast_features")

DOWN_SQL = """
DROP TABLE IF EXISTS forecast_features CASCADE;
DROP TABLE IF EXISTS feature_sets CASCADE;
"""


def upgrade() -> None:
    op.execute(UP_SQL)
    for table in RLS_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY tenant_isolation ON {table} USING ({POLICY}) WITH CHECK ({POLICY})")
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO app_rw")
        op.execute(f"GRANT SELECT ON {table} TO app_ro")


def downgrade() -> None:
    op.execute(DOWN_SQL)
