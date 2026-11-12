"""Validate weather_forecast.xlsx against coffee_shop_sales.xlsx (CS-001).

Read-only. Re-derives the sales aggregates that build_weather_forecast.py computes and
asserts the join is numerically correct, then checks sheet invariants, daily integrity,
weather-code completeness and coverage. Prints PASS/FAIL per check, writes a Markdown
report to ../coffee-scheduler/VALIDATION.md, and exits non-zero if any check fails.

Run:  python3 validate_weather_forecast.py      (needs: pandas, openpyxl)
Mirrors the build logic in build_weather_forecast.py (same directory).
"""
import os
import datetime as dt
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
SALES = os.path.join(HERE, "coffee_shop_sales.xlsx")
OUT = os.path.join(HERE, "weather_forecast.xlsx")
REPORT = os.path.normpath(os.path.join(HERE, "..", "coffee-scheduler", "VALIDATION.md"))

STORES = {3: "Astoria", 5: "Lower Manhattan", 8: "Hell's Kitchen"}
EXPECTED_START = dt.date(2023, 1, 1)
EXPECTED_END = dt.date(2023, 6, 30)
REV_TOL = 0.005           # float rounding tolerance for a single hour's revenue
DAILY_TOL = 0.01          # tolerance for summed daily revenue
SAMPLE_N = 30
SEED = 42

results = []  # (name, passed: bool, lines: list[str])


def check(name):
    def add(passed, lines):
        results.append((name, passed, lines if isinstance(lines, list) else [lines]))
    return add


def load():
    s = pd.read_excel(SALES)
    s["hour"] = s.transaction_time.apply(lambda t: t.hour)
    s["date"] = pd.to_datetime(s.transaction_date).dt.normalize()
    # Full-hour sales aggregate (all hours), matching build_weather_forecast.py semantics.
    g = (s.groupby(["store_id", "date", "hour"])
           .agg(line_items=("transaction_id", "count"),
                items=("transaction_qty", "sum"),
                revenue=("unit_price", lambda x: (x * s.loc[x.index, "transaction_qty"]).sum()))
           .reset_index())
    xl = pd.ExcelFile(OUT)
    sheet_names = xl.sheet_names
    hourly = pd.read_excel(OUT, sheet_name="Hourly_Sales_Weather")
    hourly["date"] = pd.to_datetime(hourly.date).dt.normalize()
    daily = pd.read_excel(OUT, sheet_name="Daily_Sales_Weather")
    daily["date"] = pd.to_datetime(daily.date).dt.normalize()
    codes = pd.read_excel(OUT, sheet_name="Weather_Codes")
    return s, g, hourly, daily, codes, sheet_names


def check_a_join(g, hourly):
    """Core spot-check: sampled hourly rows re-aggregate correctly from sales."""
    add = check("A. Hourly join correctness")
    gi = g.set_index(["store_id", "date", "hour"])
    trading = hourly[hourly.in_trading_hours == True]  # noqa: E712
    sample = trading.sample(n=min(SAMPLE_N, len(trading)), random_state=SEED)
    # Deterministic edges: first/last date per store at a mid hour.
    edges = []
    for sid in STORES:
        sub = trading[trading.location_id == sid]
        if len(sub):
            edges += [sub.sort_values("date").iloc[0], sub.sort_values("date").iloc[-1]]
    rows = pd.concat([sample, pd.DataFrame(edges)]).drop_duplicates(subset=["location_id", "date", "hour"])
    mism = []
    for _, r in rows.iterrows():
        key = (r.location_id, r.date, int(r.hour))
        if key not in gi.index:
            mism.append(f"{key}: no matching sales aggregate (sheet shows line_items={r.line_items})")
            continue
        src = gi.loc[key]
        if int(src.line_items) != int(r.line_items) or int(src["items"]) != int(r["items"]) \
                or abs(float(src.revenue) - float(r.revenue)) > REV_TOL:
            mism.append(f"{key}: sheet(li={r.line_items},it={r['items']},rev={r.revenue:.2f}) "
                        f"vs sales(li={int(src.line_items)},it={int(src['items'])},rev={float(src.revenue):.2f})")
    passed = not mism
    lines = [f"Checked {len(rows)} rows ({len(sample)} random seed={SEED} + {len(edges)} edge), 0 mismatches."] if passed \
        else [f"{len(mism)} mismatch(es) of {len(rows)} checked:"] + mism[:10]
    add(passed, lines)


def check_b_invariants(hourly):
    add = check("B. Hour-window & zero-fill invariants")
    bad_hours = hourly[(hourly.hour < 6) | (hourly.hour > 22)]
    zero = hourly[hourly.line_items == 0]
    bad_zero = zero[(zero.in_trading_hours != False) | (zero.revenue != 0)]  # noqa: E712
    nonzero = hourly[hourly.line_items > 0]
    bad_nonzero = nonzero[nonzero.in_trading_hours != True]  # noqa: E712
    passed = bad_hours.empty and bad_zero.empty and bad_nonzero.empty
    add(passed, [
        f"Rows outside hours 6-22: {len(bad_hours)} (expect 0).",
        f"line_items==0 rows with in_trading_hours!=False or revenue!=0: {len(bad_zero)} (expect 0).",
        f"line_items>0 rows with in_trading_hours!=True: {len(bad_nonzero)} (expect 0).",
    ])


def check_c_daily(g, hourly, daily):
    add = check("C. Daily integrity")
    lines = []
    passed = True
    # Row/shape.
    n_ok = len(daily) == 543
    stores_ok = set(daily.location_id.unique()) == set(STORES)
    lines.append(f"Daily rows: {len(daily)} (expect 543 = 3 stores x 181 days); "
                 f"stores {sorted(int(x) for x in daily.location_id.unique())}.")
    passed &= n_ok and stores_ok
    # Continuous dates per store, no gaps.
    for sid in STORES:
        dates = pd.to_datetime(daily[daily.location_id == sid].date).dt.date
        full = pd.date_range(EXPECTED_START, EXPECTED_END, freq="D").date
        missing = sorted(set(full) - set(dates))
        if missing:
            passed = False
            lines.append(f"  store {sid}: {len(missing)} missing date(s), e.g. {missing[:3]}.")
        else:
            lines.append(f"  store {sid}: {len(dates)} dates, continuous, no gaps.")
    # Daily == sum of hourly sheet per store/date (daily is DERIVED from the 6-22 hourly frame).
    hsum = (hourly.groupby(["location_id", "date"])
                  .agg(line_items=("line_items", "sum"), items=("items", "sum"), revenue=("revenue", "sum"))
                  .reset_index())
    m = daily.merge(hsum, on=["location_id", "date"], suffixes=("_d", "_h"))
    dmism = m[(m.line_items_d != m.line_items_h) | (m["items_d"] != m["items_h"])
              | ((m.revenue_d - m.revenue_h).abs() > DAILY_TOL)]
    if len(dmism):
        passed = False
        lines.append(f"Daily vs hourly-sheet sum: {len(dmism)} mismatch(es) (expect 0).")
    else:
        lines.append(f"Daily == sum(hourly sheet) for all {len(m)} store-days.")
    # Informational: sales outside the 6-22 window that daily therefore omits.
    oow = g[(g.hour < 6) | (g.hour > 22)]
    lines.append(f"[info] Sales rows outside hours 6-22 (omitted from sheets): {len(oow)} "
                 f"rows, {int(oow.line_items.sum())} line_items, ${oow.revenue.sum():,.2f} revenue.")
    add(passed, lines)


def check_d_codes(hourly, codes):
    add = check("D. Weather_Codes completeness")
    present = set(pd.to_numeric(hourly.weather_code, errors="coerce").dropna().astype(int).unique())
    known = set(codes.weather_code.astype(int).unique())
    orphan = sorted(present - known)
    # weather_desc / weather_bucket in hourly agree with the lookup.
    lut = codes.set_index(codes.weather_code.astype(int))
    desc_bad = buck_bad = 0
    for _, r in hourly.iterrows():
        wc = int(r.weather_code)
        if wc in lut.index:
            if str(r.weather_desc) != str(lut.loc[wc, "description"]):
                desc_bad += 1
            if str(r.weather_bucket) != str(lut.loc[wc, "bucket"]):
                buck_bad += 1
    passed = not orphan and desc_bad == 0 and buck_bad == 0
    add(passed, [
        f"Distinct codes in hourly: {len(present)}; in lookup: {len(known)}; orphans: {orphan or 'none'}.",
        f"weather_desc disagreements: {desc_bad}; weather_bucket disagreements: {buck_bad} (expect 0).",
    ])


def check_e_coverage(hourly, daily):
    add = check("E. Coverage")
    hs = set(int(x) for x in hourly.location_id.unique())
    ds = set(int(x) for x in daily.location_id.unique())
    dmin, dmax = hourly.date.min().date(), hourly.date.max().date()
    passed = hs == set(STORES) and ds == set(STORES) and dmin == EXPECTED_START and dmax == EXPECTED_END
    add(passed, [
        f"Stores hourly={sorted(hs)} daily={sorted(ds)} (expect [3, 5, 8]).",
        f"Date range {dmin} .. {dmax} (expect {EXPECTED_START} .. {EXPECTED_END}).",
    ])


def write_report(sheet_names):
    ok = all(p for _, p, _ in results)
    ts = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    def mtime(p):
        return dt.datetime.fromtimestamp(os.path.getmtime(p)).strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "# Weather×Sales Validation Report (CS-001)",
        "",
        f"Generated: {ts} by `data/validate_weather_forecast.py`.",
        f"Overall: **{'PASS' if ok else 'FAIL'}** ({sum(p for _, p, _ in results)}/{len(results)} checks passed).",
        "",
        "| File | Modified |",
        "| --- | --- |",
        f"| `coffee_shop_sales.xlsx` | {mtime(SALES)} |",
        f"| `weather_forecast.xlsx` | {mtime(OUT)} |",
        "",
        f"Sheets in `weather_forecast.xlsx`: {', '.join(sheet_names)} "
        "(the `README` sheet is expected — documents source/units/license — not a defect).",
        "",
    ]
    for name, passed, detail in results:
        lines.append(f"## {'✅' if passed else '❌'} {name} — {'PASS' if passed else 'FAIL'}")
        lines += [f"- {d}" for d in detail]
        lines.append("")
    with open(REPORT, "w") as f:
        f.write("\n".join(lines))
    return ok


def main():
    s, g, hourly, daily, codes, sheet_names = load()
    check_a_join(g, hourly)
    check_b_invariants(hourly)
    check_c_daily(g, hourly, daily)
    check_d_codes(hourly, codes)
    check_e_coverage(hourly, daily)
    print("=" * 64)
    for name, passed, detail in results:
        print(f"[{'PASS' if passed else 'FAIL'}] {name}")
        for d in detail:
            print(f"        {d}")
    ok = write_report(sheet_names)
    print("=" * 64)
    print(f"Report written to {REPORT}")
    print(f"OVERALL: {'PASS' if ok else 'FAIL'}")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
