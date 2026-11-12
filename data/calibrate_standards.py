"""CS-008 calibration: throughput, weather multipliers, and target confirmation.

Deterministic. Derives values from the 2023 sales x weather join, auto-applies them to
coffee_shop_labor_standards.xlsx (basis ASSUMPTION -> CALIBRATED), and appends an audit row to
Calibration_Log for every change. See the plan for method.

Run:  python3 calibrate_standards.py      (needs: pandas, openpyxl)
Reads (read-only): coffee_shop_sales.xlsx, weather_forecast.xlsx.
Writes: coffee_shop_labor_standards.xlsx (Throughput_Standards, Demand_Adjustments, Calibration_Log).
"""
import os
import datetime as dt
import pandas as pd
from openpyxl import load_workbook

HERE = os.path.dirname(os.path.abspath(__file__))
STD = os.path.join(HERE, "coffee_shop_labor_standards.xlsx")
SALES = os.path.join(HERE, "coffee_shop_sales.xlsx")
WEATHER = os.path.join(HERE, "weather_forecast.xlsx")

TODAY = dt.date(2026, 11, 12)
UTIL = 0.85
MIN_N = 5                       # min condition days to trust a derived multiplier
CLAMP = (0.5, 1.5)
HOLDOUT_DAYS = 28              # last 4 weeks per store
BASKET_FALLBACK = 1.5

WEATHER_ADJ = {               # adjustment_id -> (condition fn on daily row, new condition text)
    "ADJ-RAIN": (lambda r: (r.precip_in > 0.08) or (r.rain_hours > 0), "Precipitation > 0.08 in/day or rain_hours > 0"),
    "ADJ-COLD": (lambda r: r.temp_min_f < 32, "temp_min_f < 32 F"),
    "ADJ-HEAT": (lambda r: r.temp_max_f > 86, "temp_max_f > 86 F"),
    "ADJ-SNOW": (lambda r: r.snow_in >= 2, "snow_in >= 2 in"),
}


def load_sales():
    s = pd.read_excel(SALES)
    s["date"] = pd.to_datetime(s.transaction_date).dt.normalize()
    s["hour"] = s.transaction_time.apply(lambda t: t.hour)
    return s


def basket_size(s):
    groups = s.groupby(["store_id", "date", "transaction_time"]).size()
    b = len(s) / len(groups) if len(groups) else BASKET_FALLBACK
    return b if b >= 1.05 else BASKET_FALLBACK


def weather_multipliers(daily):
    extreme = daily.apply(lambda r: any(fn(r) for fn, _ in WEATHER_ADJ.values()), axis=1)
    normal = daily[~extreme]
    base = (normal if len(normal) else daily).groupby(["location_id", "day_of_week"]).items.median()
    def ratio(r):
        b = base.get((r.location_id, r.day_of_week))
        return (r["items"] / b) if b else None
    daily = daily.copy()
    daily["ratio"] = daily.apply(ratio, axis=1)
    out = {}
    for adj, (fn, _) in WEATHER_ADJ.items():
        days = daily[daily.apply(fn, axis=1) & daily.ratio.notna()]
        n = len(days)
        mult = float(days.ratio.median()) if n else None
        if mult is not None:
            mult = max(CLAMP[0], min(CLAMP[1], round(mult, 3)))
        out[adj] = {"multiplier": mult, "n": n}
    return out, base


def throughput_capacity(s, sec, overhead_corr, floor):
    """Return a global scale so modeled P95 hourly labor-seconds fits capacity at UTIL, per store."""
    hourly = s.groupby(["store_id", "date", "hour", "product_category"]).transaction_qty.sum().reset_index()
    lines = s.groupby(["store_id", "date", "hour"]).size().rename("lines").reset_index()
    hourly["lab"] = hourly.apply(lambda r: r.transaction_qty * sec.get(r.product_category, 0), axis=1)
    load = hourly.groupby(["store_id", "date", "hour"]).lab.sum().reset_index().merge(lines, on=["store_id", "date", "hour"])
    load["labor_sec"] = load.lab + load.lines * overhead_corr
    scale, detail = 1.0, {}
    for sid, cap_floor in floor.items():
        p95 = load[load.store_id == sid].labor_sec.quantile(0.95)
        avail = cap_floor * 3600 * UTIL
        detail[sid] = {"p95_modeled_sec": round(p95), "available_sec": round(avail), "fits": p95 <= avail}
        if p95 > avail:
            scale = min(scale, avail / p95)
    return round(scale, 4), detail


def main():
    s = load_sales()
    xl = pd.ExcelFile(WEATHER)
    daily = xl.parse("Daily_Sales_Weather")
    daily["day_of_week"] = pd.to_datetime(daily.date).dt.day_name()

    wb = load_workbook(STD)
    sp = wb["Store_Profile"]
    sph = {c.value: i for i, c in enumerate(sp[1])}
    floor = {}
    for r in sp.iter_rows(min_row=2, values_only=True):
        if r[sph["location_id"]] is not None:
            floor[r[sph["location_id"]]] = r[sph["max_staff_on_floor"]]

    # --- Throughput baseline from the sheet ---
    tp = wb["Throughput_Standards"]
    tph = {c.value: i + 1 for i, c in enumerate(tp[1])}
    sec, overhead_row = {}, None
    for r in range(2, tp.max_row + 1):
        cat = tp.cell(r, tph["product_category"]).value
        if cat is None:
            continue
        val = tp.cell(r, tph["seconds_per_unit"]).value
        if str(cat).startswith("(per transaction"):
            overhead_row = (r, float(val))
        else:
            sec[cat] = float(val)

    B = basket_size(s)
    overhead_old = overhead_row[1]
    overhead_corr = round(overhead_old / B, 2)
    scale, cap_detail = throughput_capacity(s, sec, overhead_corr, floor)

    # Final throughput values.
    sec_final = {c: max(1, round(v * scale)) for c, v in sec.items()}
    overhead_final = round(overhead_corr * scale, 2)

    # --- Weather multipliers ---
    mults, _ = weather_multipliers(daily)

    # --- Back-test (last 4 weeks per store): baseline (store,dow) vs weather-adjusted MAPE ---
    daily2 = daily.copy()
    daily2["date"] = pd.to_datetime(daily2.date).dt.normalize()
    mape = {}
    for sid in floor:
        d = daily2[daily2.location_id == sid].sort_values("date")
        cut = d.date.max() - pd.Timedelta(days=HOLDOUT_DAYS - 1)
        train, test = d[d.date < cut], d[d.date >= cut]
        base = train.groupby("day_of_week").items.median()
        def pred(row, weather):
            p = base.get(row.day_of_week)
            if p is None:
                return None
            if weather:
                for adj, (fn, _) in WEATHER_ADJ.items():
                    if fn(row) and mults[adj]["multiplier"]:
                        p *= mults[adj]["multiplier"]
            return p
        def mape_of(weather):
            errs = []
            for _, row in test.iterrows():
                pr = pred(row, weather)
                if pr and row["items"]:
                    errs.append(abs(pr - row["items"]) / row["items"])
            return round(100 * sum(errs) / len(errs), 2) if errs else None
        mape[sid] = {"baseline": mape_of(False), "weather": mape_of(True), "n_test": len(test)}

    # ---------------- Apply + log ----------------
    log = wb["Calibration_Log"]
    lh = {c.value: i + 1 for i, c in enumerate(log[1])}
    # next id
    existing = [log.cell(r, lh["calibration_id"]).value for r in range(2, log.max_row + 1)]
    nums = [int(str(x).split("-")[1]) for x in existing if x and str(x).startswith("CAL-")]
    seq = max(nums) + 1 if nums else 1
    append_row = log.max_row + 1

    def log_change(table, key, planned, observed, proposed, period, note):
        nonlocal seq, append_row
        row = {"calibration_id": f"CAL-{seq:04d}", "table": table, "row_key": key,
               "planned_value": planned, "observed_value": observed, "sample_period": period,
               "proposed_value": proposed, "approved_by": "calibration-script",
               "approved_on": TODAY, "notes": note}
        for name, col in lh.items():
            log.cell(append_row, col, row.get(name))
        seq += 1
        append_row += 1

    sales_period = f"{s.date.min().date()}..{s.date.max().date()}"

    # Throughput: fill units_per_labor_hour, apply scaled seconds, flip basis.
    for r in range(2, tp.max_row + 1):
        cat = tp.cell(r, tph["product_category"]).value
        if cat is None:
            continue
        if str(cat).startswith("(per transaction"):
            tp.cell(r, tph["seconds_per_unit"], overhead_final)
            tp.cell(r, tph["units_per_labor_hour"], round(3600 / overhead_final, 1))
            tp.cell(r, tph["basis"], "CALIBRATED")
            log_change("Throughput_Standards", "(per transaction overhead)", overhead_old,
                       f"basket B={round(B,2)}, scale={scale}", overhead_final, sales_period,
                       f"Overhead amortized by basket size then capacity-scaled x{scale}")
        else:
            new = sec_final[cat]
            tp.cell(r, tph["seconds_per_unit"], new)
            tp.cell(r, tph["units_per_labor_hour"], round(3600 / new, 1))
            tp.cell(r, tph["basis"], "CALIBRATED")
            note = ("units_per_labor_hour=3600/sec; seconds unchanged (fit capacity at x1.0)"
                    if scale == 1.0 else f"units_per_labor_hour=3600/sec; seconds capacity-scaled x{scale}")
            log_change("Throughput_Standards", cat, sec[cat], f"capacity scale x{scale}", new,
                       sales_period, note)

    # Weather multipliers — GATE on the back-test: only apply if the weather-adjusted model
    # does not worsen holdout MAPE vs the (store,dow) baseline. With 6 months of Jan-Jun data,
    # weather is confounded with season, so derived multipliers typically fail this gate.
    base_mape = [m["baseline"] for m in mape.values() if m["baseline"] is not None]
    wx_mape = [m["weather"] for m in mape.values() if m["weather"] is not None]
    backtest_ok = bool(wx_mape) and (sum(wx_mape) / len(wx_mape)) <= (sum(base_mape) / len(base_mape))

    da = wb["Demand_Adjustments"]
    dh = {c.value: i + 1 for i, c in enumerate(da[1])}
    for r in range(2, da.max_row + 1):
        adj = da.cell(r, dh["adjustment_id"]).value
        if adj in WEATHER_ADJ:
            info = mults[adj]
            old = da.cell(r, dh["demand_multiplier"]).value
            da.cell(r, dh["condition"], WEATHER_ADJ[adj][1])   # fix metric->imperial regardless
            derived = (f"{info['n']} days"
                       + (f", median {info['multiplier']}" if info['multiplier'] is not None else ""))
            if backtest_ok and info["multiplier"] is not None and info["n"] >= MIN_N:
                da.cell(r, dh["demand_multiplier"], info["multiplier"])
                da.cell(r, dh["basis"], "CALIBRATED")
                log_change("Demand_Adjustments", adj, old, derived, info["multiplier"],
                           sales_period, "Derived vs (store,dow) normal-day baseline; back-test validated")
            else:
                reason = (f"low-n (n={info['n']}<{MIN_N})" if info["multiplier"] is None or info["n"] < MIN_N
                          else "back-test did not improve MAPE (weather confounded with season)")
                log_change("Demand_Adjustments", adj, old, derived, old, sales_period,
                           f"Kept assumption {old} + fixed units: {reason}")

    wb.save(STD)
    n_logged = seq - (max(nums) + 1 if nums else 1)

    print("=== CS-008 calibration summary ===")
    print(f"Weather back-test gate: {'PASS (multipliers applied)' if backtest_ok else 'FAIL (multipliers NOT applied; assumptions kept)'}")
    print(f"Basket size B={round(B,2)}; overhead {overhead_old}s -> {overhead_final}s; throughput scale x{scale}")
    for sid, d in cap_detail.items():
        print(f"  store {sid}: peak P95 modeled {d['p95_modeled_sec']}s vs available {d['available_sec']}s -> {'fits' if d['fits'] else 'SCALED'}")
    print("Weather multipliers (derived):")
    for adj, info in mults.items():
        applied = backtest_ok and info["multiplier"] is not None and info["n"] >= MIN_N
        print(f"  {adj}: n={info['n']} median={info['multiplier']} -> {'APPLIED' if applied else 'kept-assumption'}")
    print("Back-test MAPE (last 4 wks):")
    for sid, m in mape.items():
        print(f"  store {sid}: baseline={m['baseline']}% weather={m['weather']}% (n={m['n_test']})")
    print(f"Appended {n_logged} Calibration_Log rows; workbook saved.")


if __name__ == "__main__":
    main()
