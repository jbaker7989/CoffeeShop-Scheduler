"""CS-044: idempotently load Excel fixtures into the F47 PostgreSQL schema.

Run: python3 scripts/etl_excel_to_postgres.py --database-url "$DATABASE_URL"
"""
import argparse
import datetime as dt
import json
import math
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import psycopg


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA = ROOT / "data"
BULK_TABLES = {"transactions", "weather_hourly", "weather_daily", "rule_eval_log"}
TENANT = "00000000-0000-0000-0000-000000000001"
LEGACY_UUID_NAMESPACE = uuid.UUID("0d5e1989-2bc3-4d60-8ddc-1d65b3b6c044")

PUBLIC_TABLES = {
    "employer_config", "stores", "store_hours", "stations", "fixed_tasks", "coverage_rules",
    "shift_rules", "cost_targets", "throughput_standards", "demand_adjustments", "taxonomy_meta",
    "product_taxonomy", "calibration_log", "employees", "labor_rules", "consent_waivers",
    "school_calendars", "school_calendar_days", "holidays", "rule_eval_log", "schedules", "shifts",
    "transactions", "weather_codes", "weather_hourly", "weather_daily",
}
LOAD_ORDER = ["weather_codes", "taxonomy_meta", "stores", "store_hours", "school_calendars",
    "school_calendar_days", "holidays", "employer_config", "stations", "fixed_tasks", "coverage_rules",
    "shift_rules", "cost_targets", "throughput_standards", "demand_adjustments", "product_taxonomy",
    "calibration_log", "employees", "labor_rules", "consent_waivers", "schedules", "shifts",
    "rule_eval_log", "transactions", "weather_hourly", "weather_daily", "employee_pii"]

KEYS = {
    "employer_config": ("tenant_id", "employer_id"), "stores": ("tenant_id", "location_id"),
    "store_hours": ("tenant_id", "location_id", "day_of_week"), "stations": ("tenant_id", "station_id"),
    "fixed_tasks": ("tenant_id", "task_id"), "coverage_rules": ("tenant_id", "rule_id"),
    "shift_rules": ("tenant_id", "rule_id"), "cost_targets": ("tenant_id", "location_id"),
    "throughput_standards": ("tenant_id", "product_category"),
    "demand_adjustments": ("tenant_id", "adjustment_id"),
    "taxonomy_meta": ("tenant_id", "taxonomy_version"), "product_taxonomy": ("tenant_id", "product_id"),
    "calibration_log": ("tenant_id", "calibration_id"), "employees": ("tenant_id", "employee_id"),
    "employee_pii": ("tenant_id", "employee_id"),
    "labor_rules": ("tenant_id", "rule_id", "effective_from"),
    "consent_waivers": ("tenant_id", "waiver_id"),
    "school_calendars": ("tenant_id", "calendar_id"),
    "school_calendar_days": ("tenant_id", "calendar_id", "date"), "holidays": ("tenant_id", "holiday_id"),
    "rule_eval_log": ("tenant_id", "eval_id", "evaluated_at"), "schedules": ("tenant_id", "schedule_id"),
    "shifts": ("tenant_id", "shift_id"),
    "transactions": ("tenant_id", "transaction_id", "transaction_date"),
    "weather_codes": ("weather_code",), "weather_hourly": ("tenant_id", "location_id", "date", "hour"),
    "weather_daily": ("tenant_id", "location_id", "date"),
}


class ETLValidationError(ValueError):
    pass


@dataclass
class Snapshot:
    tables: dict


@dataclass
class LoadReport:
    source_counts: dict
    target_counts: dict
    anomalies: list


def null(value):
    if value is None or pd.isna(value):
        return None
    return int(value) if isinstance(value, float) and value.is_integer() else value


def rows(path, sheet):
    frame = pd.read_excel(path, sheet_name=sheet)
    return [{str(k): null(v) for k, v in row.items()} for row in frame.to_dict("records")]


def employee_id(value):
    value = null(value)
    if value is None:
        return None
    match = re.search(r"(\d+)$", str(value))
    if not match:
        raise ETLValidationError(f"employee_id {value!r} has no numeric suffix")
    return int(match.group(1))


def legacy_uuid(value):
    return None if null(value) is None else uuid.uuid5(LEGACY_UUID_NAMESPACE, str(value))


def rename(row, names):
    return {target: row.get(source) for source, target in names.items()}


def with_tenant(items):
    return [{"tenant_id": TENANT, **item} for item in items]


def read_snapshot(data_dir=DEFAULT_DATA, include_pii=False):
    data_dir = Path(data_dir)
    standards, rules, staff, sales, weather = (data_dir / "coffee_shop_labor_standards.xlsx",
        data_dir / "coffee_shop_labor_rules.xlsx", data_dir / "coffee_shop_staff.xlsx",
        data_dir / "coffee_shop_sales.xlsx", data_dir / "weather_forecast.xlsx")
    needed = [standards, rules, staff, sales, weather]
    if any(not path.exists() for path in needed):
        raise ETLValidationError(f"missing required workbook(s): {[str(p) for p in needed if not p.exists()]}")
    t = {}
    profile = rows(standards, "Store_Profile")
    t["stores"] = with_tenant([rename(r, {"location_id":"location_id", "store_location":"store_location",
        "max_staff_on_floor":"max_staff_on_floor", "stations":"stations", "profile_type":"profile_type", "basis":"basis"})
        for _, r in sorted({r["location_id"]: r for r in profile}.items())])
    t["store_hours"] = with_tenant([rename(r, {"location_id":"location_id", "day_of_week":"day_of_week",
        "open_time":"open_time", "close_time":"close_time", "pre_open_minutes":"pre_open_minutes",
        "post_close_minutes":"post_close_minutes", "first_staff_in":"first_staff_in", "last_staff_out":"last_staff_out"}) for r in profile])
    direct = {"Employer_Config":"employer_config", "Stations":"stations", "Fixed_Tasks":"fixed_tasks",
        "Coverage_Rules":"coverage_rules", "Shift_Rules":"shift_rules", "Cost_Targets":"cost_targets",
        "Demand_Adjustments":"demand_adjustments", "Calibration_Log":"calibration_log",
        "Product_Taxonomy":"product_taxonomy", "Taxonomy_Meta":"taxonomy_meta"}
    for sheet, table in direct.items():
        t[table] = with_tenant(rows(standards if sheet not in {"Employer_Config"} else rules, sheet))
    # The workbooks use ALL for rules/tasks applying across every location; F47 models that as NULL.
    for table in ("stations", "coverage_rules", "demand_adjustments"):
        for row in t[table]:
            if not isinstance(row.get("location_id"), (int, float)):
                row["location_id"] = None
    fixed = []
    for row in t["fixed_tasks"]:
        raw_locations = str(row.get("location_id"))
        locations = raw_locations.split(",") if raw_locations not in {"ALL", "None"} else [None]
        for location in locations:
            item = dict(row)
            item["location_id"] = int(location) if location is not None else None
            item["task_id"] = f"{location or 'ALL'}:{row['task_id']}"
            fixed.append(item)
    t["fixed_tasks"] = fixed
    # The source reuses labels such as ESPRESSO-1 at multiple locations, while migration 0001
    # keys stations by (tenant_id, station_id). Namespace the imported ID to retain every row.
    for row in t["stations"]:
        row["station_id"] = f"{row['location_id']}:{row['station_id']}"
    t["throughput_standards"] = with_tenant([rename(r, {"product_category":"product_category", "prep_role":"prep_role",
        "items_sold_jan–jun_2023":"items_sold_jan_jun_2023", "seconds_per_unit":"seconds_per_unit",
        "units_per_labor_hour":"units_per_labor_hour", "basis":"basis", "notes":"notes"}) for r in rows(standards, "Throughput_Standards")])
    staff_rows = rows(staff, "Staff")
    t["employees"] = with_tenant([{**r, "employee_id": employee_id(r["employee_id"])} for r in staff_rows])
    school = rows(rules, "School_Calendar")
    t["school_calendars"] = with_tenant([rename(r, {"calendar_id":"calendar_id", "calendar_source":"calendar_source",
        "source_url":"source_url", "effective_from":"effective_from", "effective_to":"effective_to",
        "is_assumption":"is_assumption", "notes":"notes"}) for _, r in sorted({r["calendar_id"]: r for r in school}.items())])
    t["school_calendar_days"] = with_tenant([rename(r, {"calendar_id":"calendar_id", "date":"date", "in_session":"in_session", "day_before_school_day":"day_before_school_day"}) for r in school])
    for sheet, table in (("Holiday_Calendar", "holidays"), ("Labor_Rules", "labor_rules"), ("Consent_Waivers", "consent_waivers")):
        source = rows(rules, sheet)
        for r in source:
            if "employee_id" in r: r["employee_id"] = employee_id(r["employee_id"])
            if table == "labor_rules" and isinstance(r.get("value_time"), str) and not re.fullmatch(r"\d{1,2}:\d{2}(:\d{2})?", r["value_time"]):
                r["value_text"] = r.get("value_text") or r["value_time"]
                r["value_time"] = None
            if table == "labor_rules" and isinstance(r.get("waiver_doc_required"), str):
                detail = r["waiver_doc_required"]
                r["waiver_doc_required"] = detail.strip().lower() not in {"", "no", "false", "n/a"}
                r["notes"] = f"{r.get('notes') or ''} waiver_doc_detail={detail}".strip()
        t[table] = with_tenant(source)
    log = rows(rules, "Rule_Eval_Log")
    t["rule_eval_log"] = with_tenant([{**r, "eval_id": legacy_uuid(r["eval_id"]), "schedule_id": legacy_uuid(r["schedule_id"]),
        "shift_id": legacy_uuid(r["shift_id"]), "employee_id": employee_id(r["employee_id"])} for r in log])
    schedule_ids = {r["schedule_id"] for r in t["rule_eval_log"] if r["schedule_id"]}
    t["schedules"] = [{"tenant_id": TENANT, "schedule_id": value, "status": "legacy-import", "solver_version": "excel-etl"} for value in schedule_ids]
    t["shifts"] = [{"tenant_id": TENANT, "shift_id": r["shift_id"], "schedule_id": r["schedule_id"]}
        for r in t["rule_eval_log"] if r["shift_id"]]
    t["transactions"] = with_tenant(rows(sales, "Transactions"))
    t["weather_codes"] = rows(weather, "Weather_Codes")
    t["weather_hourly"] = with_tenant([{k: v for k, v in r.items() if k not in {"day_of_week", "store_location"}} for r in rows(weather, "Hourly_Sales_Weather")])
    t["weather_daily"] = with_tenant([{k: v for k, v in r.items() if k not in {"day_of_week", "store_location"}} for r in rows(weather, "Daily_Sales_Weather")])
    if include_pii:
        pii = data_dir / "private" / "coffee_shop_staff_pii.xlsx"
        if not pii.exists(): raise ETLValidationError("--include-pii requested but private PII workbook is missing")
        t["employee_pii"] = with_tenant([rename(r, {"employee_id":"employee_id", "ssn":"ssn", "line1-address":"line1",
            "line2-address":"line2", "city-address":"city", "state-address":"state", "zipcode-address":"zipcode"}) for r in rows(pii, "Staff_PII")])
        for r in t["employee_pii"]: r["employee_id"] = employee_id(r["employee_id"])
    snapshot = Snapshot(t)
    validate_snapshot(snapshot)
    return snapshot


def validate_snapshot(snapshot):
    for table in PUBLIC_TABLES & set(snapshot.tables):
        keys = KEYS[table]
        seen = set()
        for row in snapshot.tables[table]:
            key = tuple(row.get(k) for k in keys)
            if any(v is None for v in key): raise ETLValidationError(f"{table} has missing key {keys}")
            if key in seen: raise ETLValidationError(f"{table} has duplicate natural key {key}")
            seen.add(key)
    for rule in snapshot.tables["labor_rules"]:
        if not rule.get("rule_id"): raise ETLValidationError("labor_rules has missing rule_id")
    stores = {r["location_id"] for r in snapshot.tables["stores"]}
    for tx in snapshot.tables["transactions"]:
        if tx["store_id"] not in stores: raise ETLValidationError(f"transactions has invalid store_id {tx['store_id']}")
    categories = {r["product_category"] for r in snapshot.tables["product_taxonomy"]}
    throughput = {r["product_category"] for r in snapshot.tables["throughput_standards"] if not str(r["product_category"]).startswith("(per transaction")}
    if categories != throughput: raise ETLValidationError(f"taxonomy/throughput category mismatch: {categories ^ throughput}")


def columns(rows):
    return list(rows[0]) if rows else []


def ident(name):
    return '"' + name.replace('"', '""') + '"'


def copy_stage(cur, table, data):
    cols = columns(data)
    stage = f"etl_{table}"
    cur.execute(f"CREATE TEMP TABLE {ident(stage)} (LIKE {ident(table)} INCLUDING DEFAULTS) ON COMMIT DROP")
    if data:
        with cur.copy(f"COPY {ident(stage)} ({', '.join(ident(c) for c in cols)}) FROM STDIN") as copy:
            for row in data: copy.write_row([row.get(c) for c in cols])
    return stage, cols


def upsert(cur, table, stage, cols):
    key = KEYS[table]
    updates = [c for c in cols if c not in key]
    conflict = ", ".join(ident(c) for c in key)
    action = "DO NOTHING" if not updates else "DO UPDATE SET " + ", ".join(f"{ident(c)}=EXCLUDED.{ident(c)}" for c in updates)
    cur.execute(f"INSERT INTO {ident(table)} ({', '.join(ident(c) for c in cols)}) SELECT {', '.join(ident(c) for c in cols)} FROM {ident(stage)} ON CONFLICT ({conflict}) {action}")


def target_count(cur, table, tenant_id):
    if table == "weather_codes":
        return cur.execute("SELECT count(*) FROM weather_codes").fetchone()[0]
    return cur.execute(f"SELECT count(*) FROM {table} WHERE tenant_id=%s", (tenant_id,)).fetchone()[0]


def load_snapshot(database_url, tenant_slug, data_dir=DEFAULT_DATA, include_pii=False, dry_run=False):
    snapshot = read_snapshot(data_dir, include_pii)
    source_counts = {table: len(rows) for table, rows in snapshot.tables.items()}
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            tenant = cur.execute("SELECT id FROM tenants WHERE slug=%s", (tenant_slug,)).fetchone()
            if not tenant: raise ETLValidationError(f"unknown tenant slug {tenant_slug!r}")
            tenant_id = tenant[0]
            for table, data in snapshot.tables.items():
                if table != "weather_codes":
                    for row in data: row["tenant_id"] = tenant_id
            stages = {}
            load_order = [table for table in LOAD_ORDER if table in snapshot.tables]
            for table in load_order:
                data = snapshot.tables[table]
                stages[table] = copy_stage(cur, table, data)
                upsert(cur, table, *stages[table])
            for table in reversed(load_order):
                stage, _ = stages[table]; keys = KEYS[table]
                joins = " AND ".join(f"s.{ident(k)}=t.{ident(k)}" for k in keys)
                scope = "TRUE" if table == "weather_codes" else "t.tenant_id=%s"
                cur.execute(f"DELETE FROM {ident(table)} t WHERE {scope} AND NOT EXISTS (SELECT 1 FROM {ident(stage)} s WHERE {joins})", (() if table == "weather_codes" else (tenant_id,)))
            counts = {table: target_count(cur, table, tenant_id) for table in snapshot.tables}
            anomalies = [f"{t}: source={source_counts[t]} target={counts[t]}" for t in counts if source_counts[t] != counts[t]]
            if anomalies: raise ETLValidationError("; ".join(anomalies))
            if dry_run: conn.rollback()
            return LoadReport(source_counts, counts, anomalies)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--tenant-slug", default="maven-demo")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--include-pii", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--report-json", type=Path)
    args = parser.parse_args()
    report = load_snapshot(args.database_url, args.tenant_slug, args.data_dir, args.include_pii, args.dry_run)
    output = {"source_counts": report.source_counts, "target_counts": report.target_counts, "anomalies": report.anomalies}
    print(json.dumps(output, default=str, sort_keys=True))
    if args.report_json: args.report_json.write_text(json.dumps(output, default=str, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
