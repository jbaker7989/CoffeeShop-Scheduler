"""0001 initial schema — multi-tenant-ready data plane (F47 / CS-047).

Creates every domain table from docs/schema.md with tenant_id + FORCE RLS, the
app_rw/app_ro/secrets_rw roles, effective-dating (DATERANGE + GiST exclusion),
range-partitioned high-volume tables (DEFAULT partitions now; monthly partitions
added later by a maintenance job), and a restricted employee_pii table.

Authored as single-statement executes so it replays cleanly via `alembic upgrade head`
on any environment (psycopg3 runs one command per execute).

Open §5 decisions resolved for this migration (see docs/schema.md):
  - store identity split into `stores` (+ `store_hours`) so FKs have a unique store key.
  - `school_calendars` (header) + `school_calendar_days` so the enrolment FK resolves.
  - natural keys kept, scoped by tenant_id; monthly partitioning deferred to ops job.
"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

TID = "tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE"

# ---- every domain table, one CREATE TABLE statement each (dependency order) ----
TABLES = [
    # global roots (no tenant_id, no RLS)
    """CREATE TABLE tenants (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        slug text UNIQUE NOT NULL,
        name text NOT NULL,
        status text NOT NULL CHECK (status IN ('trial','active','suspended','churned')),
        plan text NOT NULL DEFAULT 'starter' CHECK (plan IN ('starter','pro','enterprise')),
        jurisdiction text NOT NULL DEFAULT 'NY-STATE+NYC',
        timezone text NOT NULL DEFAULT 'America/New_York',
        created_at timestamptz NOT NULL DEFAULT now(),
        settings jsonb NOT NULL DEFAULT '{}')""",
    """CREATE TABLE weather_codes (
        weather_code int PRIMARY KEY, description text, bucket text)""",
    # taxonomy (meta before taxonomy)
    f"""CREATE TABLE taxonomy_meta (
        {TID}, taxonomy_version text NOT NULL, source_period text, generated_on date,
        n_products int, n_types int, n_categories int,
        PRIMARY KEY (tenant_id, taxonomy_version))""",
    # store identity + per-day hours
    f"""CREATE TABLE stores (
        {TID}, location_id int NOT NULL, store_location text, max_staff_on_floor int,
        stations int, profile_type text, basis text,
        PRIMARY KEY (tenant_id, location_id))""",
    f"""CREATE TABLE store_hours (
        {TID}, location_id int NOT NULL, day_of_week text NOT NULL, open_time time, close_time time,
        pre_open_minutes int, post_close_minutes int, first_staff_in time, last_staff_out time,
        PRIMARY KEY (tenant_id, location_id, day_of_week),
        FOREIGN KEY (tenant_id, location_id) REFERENCES stores (tenant_id, location_id) ON DELETE CASCADE)""",
    # calendars
    f"""CREATE TABLE school_calendars (
        {TID}, calendar_id text NOT NULL, calendar_source text, source_url text,
        effective_from date, effective_to date, is_assumption boolean, notes text,
        PRIMARY KEY (tenant_id, calendar_id))""",
    f"""CREATE TABLE school_calendar_days (
        {TID}, calendar_id text NOT NULL, date date NOT NULL,
        in_session boolean, day_before_school_day boolean,
        PRIMARY KEY (tenant_id, calendar_id, date),
        FOREIGN KEY (tenant_id, calendar_id) REFERENCES school_calendars (tenant_id, calendar_id) ON DELETE CASCADE)""",
    f"""CREATE TABLE holidays (
        {TID}, holiday_id text NOT NULL, date date, holiday text, jurisdiction text,
        calendar_source text, source_url text, effective_from date, effective_to date,
        is_assumption boolean, notes text,
        PRIMARY KEY (tenant_id, holiday_id))""",
    # org / standards
    f"""CREATE TABLE employer_config (
        {TID}, employer_id text NOT NULL, employer_name text, jurisdiction text, wage_order text,
        fww_covered boolean, chain_location_count int, notes text,
        PRIMARY KEY (tenant_id, employer_id))""",
    f"""CREATE TABLE stations (
        {TID}, station_id text NOT NULL, location_id int, store_location text, station_type text,
        staffed_by_role text,
        PRIMARY KEY (tenant_id, station_id),
        FOREIGN KEY (tenant_id, location_id) REFERENCES stores (tenant_id, location_id) ON DELETE CASCADE)""",
    f"""CREATE TABLE fixed_tasks (
        {TID}, task_id text NOT NULL, location_id int, task text, role text, duration_minutes int,
        timing text, frequency text, basis text,
        PRIMARY KEY (tenant_id, task_id),
        FOREIGN KEY (tenant_id, location_id) REFERENCES stores (tenant_id, location_id) ON DELETE CASCADE)""",
    f"""CREATE TABLE coverage_rules (
        {TID}, rule_id text NOT NULL, location_id int, description text, value text, unit text, basis text,
        PRIMARY KEY (tenant_id, rule_id),
        FOREIGN KEY (tenant_id, location_id) REFERENCES stores (tenant_id, location_id) ON DELETE CASCADE)""",
    f"""CREATE TABLE shift_rules (
        {TID}, rule_id text NOT NULL, description text, value text, unit text, basis text,
        PRIMARY KEY (tenant_id, rule_id))""",
    f"""CREATE TABLE cost_targets (
        {TID}, location_id int NOT NULL, store_location text, target_labor_pct_of_sales numeric,
        weekly_overtime_budget_usd numeric(10,2), understaffing_penalty_weight numeric,
        premium_penalty_weight numeric, basis text,
        PRIMARY KEY (tenant_id, location_id),
        FOREIGN KEY (tenant_id, location_id) REFERENCES stores (tenant_id, location_id) ON DELETE CASCADE)""",
    f"""CREATE TABLE throughput_standards (
        {TID}, product_category text NOT NULL, prep_role text, items_sold_jan_jun_2023 int,
        seconds_per_unit numeric, units_per_labor_hour numeric, basis text, notes text,
        PRIMARY KEY (tenant_id, product_category))""",
    f"""CREATE TABLE demand_adjustments (
        {TID}, adjustment_id text NOT NULL, trigger_type text, condition text, location_id int,
        demand_multiplier numeric, staff_delta int, applies_hours text, basis text, notes text,
        PRIMARY KEY (tenant_id, adjustment_id),
        CHECK (demand_multiplier IS NULL OR demand_multiplier BETWEEN 0.5 AND 1.5),
        FOREIGN KEY (tenant_id, location_id) REFERENCES stores (tenant_id, location_id) ON DELETE CASCADE)""",
    f"""CREATE TABLE product_taxonomy (
        {TID}, product_id int NOT NULL, product_detail text, product_type text,
        product_category text NOT NULL, taxonomy_version text,
        PRIMARY KEY (tenant_id, product_id),
        FOREIGN KEY (tenant_id, taxonomy_version) REFERENCES taxonomy_meta (tenant_id, taxonomy_version))""",
    f"""CREATE TABLE calibration_log (
        {TID}, calibration_id text NOT NULL, "table" text, row_key text, planned_value text,
        observed_value text, sample_period text, proposed_value text, approved_by text,
        approved_on date, notes text,
        PRIMARY KEY (tenant_id, calibration_id))""",
    # people
    f"""CREATE TABLE employees (
        {TID}, employee_id int NOT NULL, first_name text, last_name text, sex text, dob date,
        role text CHECK (role IN ('Barista','Register','Shift lead')),
        skill_level int CHECK (skill_level BETWEEN 1 AND 3),
        home_location_id int, cross_location_eligible boolean, is_key_holder boolean,
        cash_authorised boolean, food_protection_cert boolean, hourly_rate numeric(10,2),
        availability_persona text, min_hours_week int, max_hours_week int,
        is_enrolled boolean, school_calendar_id text, grade_band text CHECK (grade_band IN ('HS','College','None')),
        has_other_employer boolean,
        PRIMARY KEY (tenant_id, employee_id),
        CHECK (min_hours_week IS NULL OR max_hours_week IS NULL OR min_hours_week <= max_hours_week),
        CHECK (is_enrolled = (school_calendar_id IS NOT NULL)),
        FOREIGN KEY (tenant_id, home_location_id) REFERENCES stores (tenant_id, location_id),
        FOREIGN KEY (tenant_id, school_calendar_id) REFERENCES school_calendars (tenant_id, calendar_id))""",
    f"""CREATE TABLE employee_pii (
        {TID}, employee_id int NOT NULL, ssn text, line1 text, line2 text, city text, state text, zipcode text,
        PRIMARY KEY (tenant_id, employee_id),
        FOREIGN KEY (tenant_id, employee_id) REFERENCES employees (tenant_id, employee_id) ON DELETE CASCADE)""",
    # rules (temporal)
    f"""CREATE TABLE labor_rules (
        {TID}, rule_id text NOT NULL, rule_category text, jurisdiction text, wage_order text,
        enforcement text, applies_age_min int, applies_age_max int, applies_school_status text,
        applies_day_type text, applies_season text, requires_employer_flag text, metric text,
        operator text, value_num numeric, value_time time, value_text text, unit text, consequence text,
        waivable_by_consent boolean, waiver_doc_required boolean, priority int,
        effective_from date NOT NULL, effective_to date, source_citation text, source_url text,
        verification_status text, verified_on date, notes text,
        validity daterange GENERATED ALWAYS AS (daterange(effective_from, effective_to, '[)')) STORED,
        PRIMARY KEY (tenant_id, rule_id, effective_from),
        CONSTRAINT labor_rules_no_overlap EXCLUDE USING gist
            (tenant_id WITH =, rule_id WITH =, validity WITH &&))""",
    f"""CREATE TABLE consent_waivers (
        {TID}, waiver_id text NOT NULL, employee_id int, rule_id text, document_type text,
        valid_from date NOT NULL, valid_to date, doc_reference text, notes text,
        validity daterange GENERATED ALWAYS AS (daterange(valid_from, valid_to, '[)')) STORED,
        PRIMARY KEY (tenant_id, waiver_id),
        FOREIGN KEY (tenant_id, employee_id) REFERENCES employees (tenant_id, employee_id) ON DELETE CASCADE,
        CONSTRAINT consent_waivers_no_overlap EXCLUDE USING gist
            (tenant_id WITH =, employee_id WITH =, rule_id WITH =, validity WITH &&))""",
    # events + solver/forecast output
    f"""CREATE TABLE events (
        {TID}, event_id uuid NOT NULL DEFAULT gen_random_uuid(), location_id int, event_type text,
        title text, start_at timestamptz, end_at timestamptz, demand_multiplier numeric,
        status text CHECK (status IN ('active','cancelled')), source text,
        PRIMARY KEY (tenant_id, event_id),
        FOREIGN KEY (tenant_id, location_id) REFERENCES stores (tenant_id, location_id) ON DELETE CASCADE)""",
    f"""CREATE TABLE schedules (
        {TID}, schedule_id uuid NOT NULL DEFAULT gen_random_uuid(), location_id int, week_start date,
        status text, created_at timestamptz NOT NULL DEFAULT now(), solver_version text,
        PRIMARY KEY (tenant_id, schedule_id),
        FOREIGN KEY (tenant_id, location_id) REFERENCES stores (tenant_id, location_id) ON DELETE CASCADE)""",
    f"""CREATE TABLE shifts (
        {TID}, shift_id uuid NOT NULL DEFAULT gen_random_uuid(), schedule_id uuid, location_id int,
        station_id text, start_at timestamptz, end_at timestamptz, role text,
        PRIMARY KEY (tenant_id, shift_id),
        FOREIGN KEY (tenant_id, schedule_id) REFERENCES schedules (tenant_id, schedule_id) ON DELETE CASCADE,
        FOREIGN KEY (tenant_id, location_id) REFERENCES stores (tenant_id, location_id) ON DELETE CASCADE)""",
    f"""CREATE TABLE shift_assignments (
        {TID}, shift_id uuid NOT NULL, employee_id int NOT NULL, is_meal_break_covered boolean,
        PRIMARY KEY (tenant_id, shift_id, employee_id),
        FOREIGN KEY (tenant_id, shift_id) REFERENCES shifts (tenant_id, shift_id) ON DELETE CASCADE,
        FOREIGN KEY (tenant_id, employee_id) REFERENCES employees (tenant_id, employee_id) ON DELETE CASCADE)""",
    f"""CREATE TABLE forecasts (
        {TID}, forecast_id uuid NOT NULL DEFAULT gen_random_uuid(), location_id int, product_category text,
        forecast_hour timestamptz, p70 numeric, lower numeric, upper numeric, model_version text,
        taxonomy_version text,
        PRIMARY KEY (tenant_id, forecast_id),
        FOREIGN KEY (tenant_id, location_id) REFERENCES stores (tenant_id, location_id) ON DELETE CASCADE)""",
    # partitioned high-volume tables (DEFAULT partition created below)
    f"""CREATE TABLE rule_eval_log (
        {TID}, eval_id uuid NOT NULL DEFAULT gen_random_uuid(), schedule_id uuid, shift_id uuid,
        employee_id int, rule_id text,
        result text CHECK (result IN ('PASS','FAIL','PREMIUM','WARN')),
        premium_cost_usd numeric(10,2), evaluated_at timestamptz NOT NULL DEFAULT now(),
        agent text, notes text,
        PRIMARY KEY (tenant_id, eval_id, evaluated_at),
        FOREIGN KEY (tenant_id, employee_id) REFERENCES employees (tenant_id, employee_id),
        FOREIGN KEY (tenant_id, shift_id) REFERENCES shifts (tenant_id, shift_id))
        PARTITION BY RANGE (evaluated_at)""",
    f"""CREATE TABLE transactions (
        {TID}, transaction_id bigint NOT NULL, transaction_date date NOT NULL, transaction_time time,
        transaction_qty int, store_id int, store_location text, product_id int, unit_price numeric(10,2),
        product_category text, product_type text, product_detail text,
        PRIMARY KEY (tenant_id, transaction_id, transaction_date),
        FOREIGN KEY (tenant_id, store_id) REFERENCES stores (tenant_id, location_id),
        FOREIGN KEY (tenant_id, product_id) REFERENCES product_taxonomy (tenant_id, product_id))
        PARTITION BY RANGE (transaction_date)""",
    f"""CREATE TABLE weather_hourly (
        {TID}, location_id int NOT NULL, date date NOT NULL, hour int NOT NULL, line_items int, items int,
        revenue numeric(10,2), temperature_2m numeric, apparent_temperature numeric,
        relative_humidity_2m numeric, precipitation numeric, rain numeric, snowfall numeric,
        snow_depth numeric, cloud_cover numeric, wind_speed_10m numeric, wind_gusts_10m numeric,
        weather_code int, weather_desc text, weather_bucket text, in_trading_hours boolean,
        PRIMARY KEY (tenant_id, location_id, date, hour),
        FOREIGN KEY (tenant_id, location_id) REFERENCES stores (tenant_id, location_id) ON DELETE CASCADE,
        FOREIGN KEY (weather_code) REFERENCES weather_codes (weather_code))
        PARTITION BY RANGE (date)""",
    f"""CREATE TABLE weather_daily (
        {TID}, location_id int NOT NULL, date date NOT NULL, line_items int, items int,
        revenue numeric(10,2), temp_max_f numeric, temp_min_f numeric, temp_mean_f numeric,
        precip_in numeric, snow_in numeric, max_wind_mph numeric, rain_hours int, dominant_weather text,
        PRIMARY KEY (tenant_id, location_id, date),
        FOREIGN KEY (tenant_id, location_id) REFERENCES stores (tenant_id, location_id) ON DELETE CASCADE)""",
]

DEFAULT_PARTITIONS = [
    "CREATE TABLE rule_eval_log_default PARTITION OF rule_eval_log DEFAULT",
    "CREATE TABLE transactions_default PARTITION OF transactions DEFAULT",
    "CREATE TABLE weather_hourly_default PARTITION OF weather_hourly DEFAULT",
]

# domain tables that get tenant_id + RLS + app-role grants (not tenants/weather_codes/employee_pii)
DOMAIN_TABLES = [
    "taxonomy_meta", "stores", "store_hours", "school_calendars", "school_calendar_days",
    "holidays", "employer_config", "stations", "fixed_tasks", "coverage_rules", "shift_rules",
    "cost_targets", "throughput_standards", "demand_adjustments", "product_taxonomy",
    "calibration_log", "employees", "labor_rules", "consent_waivers", "events", "schedules",
    "shifts", "shift_assignments", "forecasts", "rule_eval_log", "transactions",
    "weather_hourly", "weather_daily",
]
APPEND_ONLY = ["calibration_log", "rule_eval_log"]
GLOBAL_READONLY = ["tenants", "weather_codes"]
# NULLIF(...,'') so an unset *or* empty app.tenant_id both yield NULL -> matches no rows
# (graceful fail-closed), rather than erroring on ''::uuid.
POLICY = ("tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid")


def upgrade() -> None:
    conn = op.get_bind()
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")

    # roles (CREATE ROLE has no IF NOT EXISTS)
    for role in ("app_rw", "app_ro", "secrets_rw"):
        exists = conn.exec_driver_sql(
            "SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)
        ).scalar()
        if not exists:
            op.execute(f"CREATE ROLE {role} NOLOGIN")

    for stmt in TABLES:
        op.execute(stmt)
    for stmt in DEFAULT_PARTITIONS:
        op.execute(stmt)

    op.execute(
        "INSERT INTO tenants (id, slug, name, status, plan, jurisdiction) VALUES "
        "('00000000-0000-0000-0000-000000000001','maven-demo','Maven Roasters (demo)',"
        "'active','pro','NY-STATE+NYC')"
    )

    # RLS + grants on every domain table
    for t in DOMAIN_TABLES:
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY tenant_isolation ON {t} USING ({POLICY}) WITH CHECK ({POLICY})")
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {t} TO app_rw")
        op.execute(f"GRANT SELECT ON {t} TO app_ro")
    for t in APPEND_ONLY:
        op.execute(f"REVOKE UPDATE, DELETE ON {t} FROM app_rw")

    # restricted PII: RLS + only secrets_rw may touch it
    op.execute("ALTER TABLE employee_pii ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE employee_pii FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation ON employee_pii USING ({POLICY}) WITH CHECK ({POLICY})")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON employee_pii TO secrets_rw")

    # global reference tables: readable by both app roles, no RLS
    for t in GLOBAL_READONLY:
        op.execute(f"GRANT SELECT ON {t} TO app_rw")
        op.execute(f"GRANT SELECT ON {t} TO app_ro")


def downgrade() -> None:
    # initial migration: drop everything it created
    for t in (["employee_pii"] + list(reversed(DOMAIN_TABLES)) + GLOBAL_READONLY):
        op.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
    for role in ("app_rw", "app_ro", "secrets_rw"):
        op.execute(f"DROP ROLE IF EXISTS {role}")
