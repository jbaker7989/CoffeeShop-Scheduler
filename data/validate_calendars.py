"""Validate the multi-year School_Calendar and Holiday_Calendar in coffee_shop_labor_rules.xlsx.

Read-only. Guards the committed calendar data (built by the codex .mjs scripts, which require
@oai/artifact-tool and cannot run in this environment). Provides the count + full-date-coverage
assertions the .mjs validator lacks. Prints PASS/FAIL per check, writes
coffee-scheduler/CALENDAR_VALIDATION.md, exits non-zero on any failure.

Run:  python3 validate_calendars.py      (needs: openpyxl)
"""
import os
import datetime as dt
from collections import Counter
from openpyxl import load_workbook

HERE = os.path.dirname(os.path.abspath(__file__))
RULES = os.path.join(HERE, "coffee_shop_labor_rules.xlsx")
REPORT = os.path.normpath(os.path.join(HERE, "..", "coffee-scheduler", "CALENDAR_VALIDATION.md"))

START = dt.date(2023, 1, 1)
END = dt.date(2028, 12, 31)
AS_OF = dt.date(2026, 11, 12)
ROSTER_FK = "NYC-DOE-2026-27"
TERM_2627 = (dt.date(2026, 9, 10), dt.date(2027, 6, 28))  # 2026-27 instructional term (per build script)
FED_NAMES = {"New Year's Day", "Martin Luther King Jr. Day", "Washington's Birthday", "Memorial Day",
             "Juneteenth National Independence Day", "Independence Day", "Labor Day", "Columbus Day",
             "Veterans Day", "Thanksgiving Day", "Christmas Day"}

results = []


def add(name, passed, lines):
    results.append((name, passed, lines if isinstance(lines, list) else [lines]))


# ---- date helpers (correct rules; Election Day = first Tue AFTER first Mon) ----
def nth_weekday(year, month, weekday, n):          # weekday: Mon=0..Sun=6
    d = dt.date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    return d + dt.timedelta(days=offset + 7 * (n - 1))


def last_weekday(year, month, weekday):
    d = dt.date(year, month + 1, 1) - dt.timedelta(days=1) if month < 12 else dt.date(year, 12, 31)
    return d - dt.timedelta(days=(d.weekday() - weekday) % 7)


def observed(d):
    if d.weekday() == 5:
        return d - dt.timedelta(days=1)
    if d.weekday() == 6:
        return d + dt.timedelta(days=1)
    return d


def federal_holidays(year):
    return {
        observed(dt.date(year, 1, 1)): "New Year's Day",
        nth_weekday(year, 1, 0, 3): "Martin Luther King Jr. Day",
        nth_weekday(year, 2, 0, 3): "Washington's Birthday",
        last_weekday(year, 5, 0): "Memorial Day",
        observed(dt.date(year, 6, 19)): "Juneteenth National Independence Day",
        observed(dt.date(year, 7, 4)): "Independence Day",
        nth_weekday(year, 9, 0, 1): "Labor Day",
        nth_weekday(year, 10, 0, 2): "Columbus Day",
        observed(dt.date(year, 11, 11)): "Veterans Day",
        nth_weekday(year, 11, 3, 4): "Thanksgiving Day",
        observed(dt.date(year, 12, 25)): "Christmas Day",
    }


def election_day(year):
    first_monday = nth_weekday(year, 11, 0, 1)
    return first_monday + dt.timedelta(days=1)


def d(v):
    return v.date() if isinstance(v, dt.datetime) else v


def load():
    wb = load_workbook(RULES, data_only=True)
    def rows(sheet):
        ws = wb[sheet]
        hdr = [c.value for c in ws[1]]
        out = []
        for r in range(2, ws.max_row + 1):
            vals = {hdr[i]: ws.cell(r, i + 1).value for i in range(len(hdr))}
            if vals.get("date") is not None:
                out.append(vals)
        return out
    return rows("School_Calendar"), rows("Holiday_Calendar")


def check_school_coverage(school):
    active = [r for r in school if r["effective_to"] in (None, "") or d(r["effective_to"]) > AS_OF]
    by_date = Counter(d(r["date"]) for r in active)
    expected = []
    cur = START
    while cur <= END:
        expected.append(cur)
        cur += dt.timedelta(days=1)
    missing = [str(x) for x in expected if by_date.get(x, 0) == 0]
    dups = [str(x) for x in expected if by_date.get(x, 0) > 1]
    out_of_range = [str(d(r["date"])) for r in active if not (START <= d(r["date"]) <= END)]
    passed = not missing and not dups and not out_of_range and len(active) == len(expected)
    add("A. School_Calendar full-date coverage (no gaps/dups/None)", passed, [
        f"Active rows: {len(active)} (expect {len(expected)} = every day {START}..{END}).",
        f"Missing dates: {len(missing)}{' e.g. '+str(missing[:3]) if missing else ''}.",
        f"Duplicate active dates: {len(dups)}{' e.g. '+str(dups[:3]) if dups else ''}.",
        f"Rows outside range: {len(out_of_range)}.",
    ])
    return active


def check_school_invariants(active):
    by_date = {d(r["date"]): r for r in active}
    wk = [str(dd) for dd, r in by_date.items() if dd.weekday() >= 5 and r["in_session"]]
    assumption_bad = [r["calendar_id"] for r in active
                      if r["is_assumption"] and "2027-28" not in str(r["calendar_id"])]
    dbs_bad = []
    for dd, r in by_date.items():
        nxt = by_date.get(dd + dt.timedelta(days=1))
        expect = bool(nxt["in_session"]) if nxt else False
        if bool(r["day_before_school_day"]) != expect:
            dbs_bad.append(str(dd))
    passed = not wk and not assumption_bad and not dbs_bad
    add("B. School_Calendar invariants (weekend/assumption/day-before)", passed, [
        f"Weekend rows marked in_session: {len(wk)} (expect 0).",
        f"is_assumption=True outside the 2027-28 planning term: {len(assumption_bad)} (expect 0).",
        f"day_before_school_day != next-day in_session: {len(dbs_bad)}{' e.g. '+str(dbs_bad[:3]) if dbs_bad else ''} (expect 0).",
    ])


def check_roster_fk(active):
    ids = {r["calendar_id"] for r in active}
    present = ROSTER_FK in ids
    term_dates = [d(r["date"]) for r in active if r["calendar_id"] == ROSTER_FK]
    lo, hi = min(term_dates), max(term_dates) if term_dates else (None, None)
    covers_term = all(r["calendar_id"] == ROSTER_FK
                      for r in active if TERM_2627[0] <= d(r["date"]) <= TERM_2627[1])
    passed = present and covers_term
    add("C. Roster FK resolves (NYC-DOE-2026-27)", passed, [
        f"calendar_id '{ROSTER_FK}' present: {present}.",
        f"Its date span: {lo}..{hi}.",
        f"Every date in the 2026-27 term {TERM_2627[0]}..{TERM_2627[1]} carries that id: {covers_term}.",
    ])


def check_holidays(holidays):
    fed_expected = {}
    for y in range(2023, 2029):
        for dd, nm in federal_holidays(y).items():
            fed_expected[dd] = nm
    fed_rows = {d(r["date"]) for r in holidays if r["jurisdiction"] == "US-FED"}
    nys_rows = [r for r in holidays if r["jurisdiction"] == "NY-STATE"]
    missing_fed = sorted(str(x) for x in fed_expected if x not in fed_rows)
    # Election Day (corrected rule) must appear among NY-STATE rows for each year.
    elect_missing = [y for y in range(2023, 2029)
                     if election_day(y) not in {d(r["date"]) for r in nys_rows}]
    # Duplication (informational): federal dates also present as NY-STATE.
    nys_dates = Counter(d(r["date"]) for r in nys_rows)
    dup = sum(1 for x in fed_expected if nys_dates.get(x, 0) >= 1)
    passed = not missing_fed and not elect_missing and len(fed_rows) == len(fed_expected)
    add("D. Holiday_Calendar (federal + Election Day)", passed, [
        f"US-FED rows: {len(fed_rows)} (expect {len(fed_expected)} = 11/yr x 6).",
        f"Missing federal holidays: {len(missing_fed)}{' e.g. '+str(missing_fed[:3]) if missing_fed else ''}.",
        f"Years missing a correct Election Day (first Tue after first Mon): {elect_missing or 'none'}.",
        f"[info] Federal dates also emitted as NY-STATE (dual-jurisdiction duplication): {dup} (by design; match holidays by date).",
    ])


def write_report():
    ok = all(p for _, p, _ in results)
    ts = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "# Multi-Year Calendar — Validation Report (CS-004 / CS-007)",
        "",
        f"Generated: {ts} by `data/validate_calendars.py` (as-of {AS_OF}).",
        f"Overall: **{'PASS' if ok else 'FAIL'}** ({sum(p for _, p, _ in results)}/{len(results)} checks passed).",
        "",
        "Validates the committed School_Calendar / Holiday_Calendar. The `.mjs` builder/validator require "
        "codex's `@oai/artifact-tool` and do not run here; this is the in-repo guard.",
        "",
    ]
    for name, passed, detail in results:
        lines.append(f"## {'✅' if passed else '❌'} {name} — {'PASS' if passed else 'FAIL'}")
        lines += [f"- {x}" for x in detail]
        lines.append("")
    with open(REPORT, "w") as f:
        f.write("\n".join(lines))
    return ok


def main():
    school, holidays = load()
    active = check_school_coverage(school)
    check_school_invariants(active)
    check_roster_fk(active)
    check_holidays(holidays)
    print("=" * 64)
    for name, passed, detail in results:
        print(f"[{'PASS' if passed else 'FAIL'}] {name}")
        for x in detail:
            print(f"        {x}")
    ok = write_report()
    print("=" * 64)
    print(f"Report written to {REPORT}")
    print(f"OVERALL: {'PASS' if ok else 'FAIL'}")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
