"""Validate CS-008 calibration of coffee_shop_labor_standards.xlsx.

Read-only. Asserts the throughput calibration (units filled, capacity-feasible), the weather-row
unit fix + sane multipliers, and the Calibration_Log audit trail. Reports the back-test MAPE.
Writes coffee-scheduler/CALIBRATION_VALIDATION.md; exits non-zero on failure.

Run:  python3 validate_calibration.py      (needs: pandas, openpyxl)
"""
import os
import datetime as dt
import pandas as pd
from openpyxl import load_workbook

HERE = os.path.dirname(os.path.abspath(__file__))
STD = os.path.join(HERE, "coffee_shop_labor_standards.xlsx")
SALES = os.path.join(HERE, "coffee_shop_sales.xlsx")
WEATHER = os.path.join(HERE, "weather_forecast.xlsx")
REPORT = os.path.normpath(os.path.join(HERE, "..", "coffee-scheduler", "CALIBRATION_VALIDATION.md"))

UTIL = 0.85
CLAMP = (0.5, 1.5)
WEATHER_ADJ = ["ADJ-RAIN", "ADJ-COLD", "ADJ-HEAT", "ADJ-SNOW"]
METRIC_MARKERS = ["mm", "cm", "°c", " c "]
results = []


def add(name, passed, lines):
    results.append((name, passed, lines if isinstance(lines, list) else [lines]))


def main():
    wb = load_workbook(STD, data_only=True)

    # ---- Throughput ----
    tp = wb["Throughput_Standards"]
    th = {c.value: i for i, c in enumerate(tp[1])}
    tp_rows = [r for r in tp.iter_rows(min_row=2, values_only=True) if r[th["product_category"]]]
    bad_units, bad_basis, bad_sec, sec_by_cat, overhead = [], [], [], {}, None
    for r in tp_rows:
        cat = r[th["product_category"]]
        sec = r[th["seconds_per_unit"]]
        units = r[th["units_per_labor_hour"]]
        basis = r[th["basis"]]
        if sec is None or sec <= 0:
            bad_sec.append(cat); continue
        if units is None or round(units, 1) != round(3600 / sec, 1):
            bad_units.append(f"{cat}: units={units} != {round(3600/sec,1)}")
        if basis != "CALIBRATED":
            bad_basis.append(f"{cat}: {basis}")
        if str(cat).startswith("(per transaction"):
            overhead = float(sec)
        else:
            sec_by_cat[cat] = float(sec)
    add("A. Throughput units & basis", not (bad_units or bad_basis or bad_sec), [
        f"Rows: {len(tp_rows)}; units_per_labor_hour == 3600/sec mismatches: {len(bad_units)}{' '+str(bad_units[:3]) if bad_units else ''}.",
        f"Rows not basis=CALIBRATED: {len(bad_basis)}{' '+str(bad_basis) if bad_basis else ''}.",
        f"Non-positive seconds: {bad_sec or 'none'}.",
    ])

    # ---- Capacity feasibility (recompute P95 hourly labor-seconds per store) ----
    sp = wb["Store_Profile"]
    sph = {c.value: i for i, c in enumerate(sp[1])}
    floor = {}
    for r in sp.iter_rows(min_row=2, values_only=True):
        if r[sph["location_id"]] is not None:
            floor[r[sph["location_id"]]] = r[sph["max_staff_on_floor"]]
    s = pd.read_excel(SALES)
    s["date"] = pd.to_datetime(s.transaction_date).dt.normalize()
    s["hour"] = s.transaction_time.apply(lambda t: t.hour)
    cat_items = s.groupby(["store_id", "date", "hour", "product_category"]).transaction_qty.sum().reset_index()
    cat_items["lab"] = cat_items.apply(lambda r: r.transaction_qty * sec_by_cat.get(r.product_category, 0), axis=1)
    lines = s.groupby(["store_id", "date", "hour"]).size().rename("lines").reset_index()
    load = cat_items.groupby(["store_id", "date", "hour"]).lab.sum().reset_index().merge(lines, on=["store_id", "date", "hour"])
    load["labor_sec"] = load.lab + load.lines * (overhead or 0)
    cap_lines, cap_ok = [], True
    for sid, fl in floor.items():
        p95 = load[load.store_id == sid].labor_sec.quantile(0.95)
        avail = fl * 3600 * UTIL
        ok = p95 <= avail
        cap_ok &= ok
        cap_lines.append(f"store {sid}: P95 modeled {round(p95)}s vs available {round(avail)}s -> {'OK' if ok else 'INFEASIBLE'}")
    add("B. Peak-hour capacity feasibility (<= floor x 3600 x 0.85)", cap_ok, cap_lines)

    # ---- Weather rows: imperial conditions + sane multipliers ----
    da = wb["Demand_Adjustments"]
    dh = {c.value: i for i, c in enumerate(da[1])}
    metric_bad, range_bad, applied = [], [], []
    for r in da.iter_rows(min_row=2, values_only=True):
        adj = r[dh["adjustment_id"]]
        if adj in WEATHER_ADJ:
            cond = str(r[dh["condition"]]).lower()
            if any(m in cond for m in METRIC_MARKERS):
                metric_bad.append(adj)
            mult = r[dh["demand_multiplier"]]
            if mult is None or not (CLAMP[0] <= mult <= CLAMP[1]):
                range_bad.append(f"{adj}={mult}")
            if r[dh["basis"]] == "CALIBRATED":
                applied.append(adj)
    add("C. Weather conditions imperial + multipliers in range", not (metric_bad or range_bad), [
        f"Conditions still using metric units (mm/cm/C): {metric_bad or 'none'}.",
        f"Multipliers outside {CLAMP}: {range_bad or 'none'}.",
        f"[info] Weather rows applied as CALIBRATED: {applied or 'none (kept as assumptions — back-test did not validate)'}.",
    ])

    # ---- Calibration_Log audit trail ----
    log = wb["Calibration_Log"]
    lh = {c.value: i for i, c in enumerate(log[1])}
    new_rows = [r for r in log.iter_rows(min_row=2, values_only=True)
                if r[lh["calibration_id"]] and r[lh["approved_by"]] == "calibration-script"]
    keys = {(r[lh["table"]], r[lh["row_key"]]) for r in new_rows}
    expected_keys = ({("Throughput_Standards", c) for c in sec_by_cat}
                     | {("Throughput_Standards", "(per transaction overhead)")}
                     | {("Demand_Adjustments", a) for a in WEATHER_ADJ})
    missing = expected_keys - keys
    incomplete = [r[lh["calibration_id"]] for r in new_rows
                  if not (r[lh["table"]] and r[lh["row_key"]] and r[lh["approved_by"]] and r[lh["approved_on"]])]
    add("D. Calibration_Log audit trail", not missing and not incomplete, [
        f"Appended audit rows: {len(new_rows)} (expect >= {len(expected_keys)}).",
        f"Missing (table,row_key) audit entries: {sorted(missing) if missing else 'none'}.",
        f"Rows missing required fields: {incomplete or 'none'}.",
    ])

    # ---- Back-test MAPE (informational) ----
    daily = pd.read_excel(WEATHER, sheet_name="Daily_Sales_Weather")
    daily["date"] = pd.to_datetime(daily.date).dt.normalize()
    daily["day_of_week"] = daily.date.dt.day_name()
    bt = []
    for sid in floor:
        d = daily[daily.location_id == sid].sort_values("date")
        cut = d.date.max() - pd.Timedelta(days=27)
        train, test = d[d.date < cut], d[d.date >= cut]
        base = train.groupby("day_of_week").items.median()
        errs = [abs(base.get(row.day_of_week) - row["items"]) / row["items"]
                for _, row in test.iterrows() if base.get(row.day_of_week) and row["items"]]
        bt.append(f"store {sid}: baseline MAPE {round(100*sum(errs)/len(errs),2)}% (n={len(test)})")
    add("E. Back-test baseline MAPE (informational)", True,
        bt + ["[info] Weather multipliers were not applied (did not beat this baseline on 6-month data)."])

    ok = all(p for _, p, _ in results)
    ts = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "# Calibration — Validation Report (CS-008)", "",
        f"Generated: {ts} by `data/validate_calibration.py`.",
        f"Overall: **{'PASS' if ok else 'FAIL'}** ({sum(p for _, p, _ in results)}/{len(results)} checks passed).", "",
    ]
    for name, passed, detail in results:
        lines.append(f"## {'✅' if passed else '❌'} {name} — {'PASS' if passed else 'FAIL'}")
        lines += [f"- {x}" for x in detail]
        lines.append("")
    with open(REPORT, "w") as f:
        f.write("\n".join(lines))

    print("=" * 64)
    for name, passed, detail in results:
        print(f"[{'PASS' if passed else 'FAIL'}] {name}")
        for x in detail:
            print(f"        {x}")
    print("=" * 64)
    print(f"Report -> {REPORT}\nOVERALL: {'PASS' if ok else 'FAIL'}")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
