"""CS-014b: versioned demand forecast model artifacts."""
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None

POLICY = "tenant_id = app_current_tenant()"

UP_SQL = """
CREATE TABLE forecast_models (
    tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    model_version text NOT NULL,
    feature_version text NOT NULL,
    approach text NOT NULL,
    taxonomy_version text NOT NULL,
    params jsonb NOT NULL,
    trained_at timestamptz NOT NULL DEFAULT now(),
    train_window_start date NOT NULL,
    train_window_end date NOT NULL,
    validation_window_start date NOT NULL,
    validation_window_end date NOT NULL,
    validation_pinball numeric NOT NULL,
    validation_coverage numeric,
    candidates jsonb NOT NULL,
    notes text,
    PRIMARY KEY (tenant_id, model_version),
    FOREIGN KEY (tenant_id, feature_version) REFERENCES feature_sets (tenant_id, feature_version),
    FOREIGN KEY (tenant_id, taxonomy_version) REFERENCES taxonomy_meta (tenant_id, taxonomy_version),
    CHECK (approach IN ('empirical_quantile','linear_quantile_regression')),
    CHECK (train_window_start <= train_window_end),
    CHECK (validation_pinball >= 0)
);
"""

DOWN_SQL = "DROP TABLE IF EXISTS forecast_models CASCADE;"


def upgrade() -> None:
    op.execute(UP_SQL)
    for table in ("forecast_models",):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY tenant_isolation ON {table} USING ({POLICY}) WITH CHECK ({POLICY})")
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO app_rw")
        op.execute(f"GRANT SELECT ON {table} TO app_ro")


def downgrade() -> None:
    op.execute(DOWN_SQL)
