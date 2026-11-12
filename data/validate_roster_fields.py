"""Validate the roster scheduling fields in coffee_shop_staff.xlsx (CS-002 + CS-003).

Read-only. Asserts schema, value domains, minor legality, per-store coverage feasibility, and
PII separation. Prints PASS/FAIL per check, writes coffee-scheduler/ROSTER_VALIDATION.md, and
exits non-zero on any failure.

Run:  python3 validate_roster_fields.py      (needs: openpyxl)
"""
import os
import datetime as dt
from openpyxl import load_workbook

HERE = os.path.dirname(os.path.abspath(__file__))
STAFF = os.path.join(HERE, "coffee_shop_staff.xlsx")
PII = os.path.join(HERE, "private", "coffee_shop_staff_pii.xlsx")
REPORT = os.path.normpath(os.path.join(HERE, "..", "coffee-scheduler", "ROSTER_VALIDATION.md"))

AS_OF = dt.date(2026, 11, 12)
STORES = {3, 5, 8}
ROLES = {"Barista", "Register", "Shift lead"}
PERSONAS = {"Full-time-open", "Part-time-mornings", "Part-time-evenings", "Weekends-only", "Student-limited"}
GRADE_BANDS = {"HS", "College", "None"}
PII_COLS = {"ssn", "line1-address", "line2-address", "city-address", "state-address", "zipcode-address"}
IDENTITY = ["employee_id", "first_name", "last_name", "sex", "dob"]
SCHED = ["role", "skill_level", "home_location_id", "cross_location_eligible", "is_key_holder",
         "cash_authorised", "food_protection_cert", "hourly_rate", "availability_persona",
         "min_hours_week", "max_hours_week", "is_enrolled", "school_calendar_id", "grade_band",
         "has_other_employer"]

results = []  # (name, passed, lines)


def add(name, passed, lines):
    results.append((name, passed, lines if isinstance(lines, list) else [lines]))


def age_on(d):
    return AS_OF.year - d.year - ((AS_OF.month, AS_OF.day) < (d.month, d.day))


def load_rows():
    wb = load_workbook(STAFF, data_only=True)
    ws = wb["Staff"]
    hdr = [c.value for c in ws[1]]
    rows = []
    for r in range(2, ws.max_row + 1):
        vals = {hdr[i]: ws.cell(r, i + 1).value for i in range(len(hdr))}
        if vals.get("employee_id"):
            dob = vals["dob"]
            vals["_age"] = age_on(dob.date() if isinstance(dob, dt.datetime) else dob)
            rows.append(vals)
    return hdr, rows, wb.sheetnames


def check_schema(hdr):
    missing = [c for c in IDENTITY + SCHED if c not in hdr]
    pii_present = [c for c in PII_COLS if c in hdr]
    identity_moved = [c for c in IDENTITY if hdr.index(c) >= 5] if not missing else []
    passed = not missing and not pii_present and not identity_moved
    add("A. Schema & column layout", passed, [
        f"Missing columns: {missing or 'none'}.",
        f"PII columns still in tracked file: {pii_present or 'none'} (expect none).",
        f"Identity cols A-E preserved in place: {'yes' if not identity_moved else 'NO '+str(identity_moved)}.",
    ])


def check_domains(rows):
    bad = []
    for e in rows:
        eid = e["employee_id"]
        if e["role"] not in ROLES: bad.append(f"{eid}: role={e['role']}")
        if e["skill_level"] not in (1, 2, 3): bad.append(f"{eid}: skill_level={e['skill_level']}")
        if e["home_location_id"] not in STORES: bad.append(f"{eid}: home_location_id={e['home_location_id']}")
        if e["availability_persona"] not in PERSONAS: bad.append(f"{eid}: persona={e['availability_persona']}")
        if e["grade_band"] not in GRADE_BANDS: bad.append(f"{eid}: grade_band={e['grade_band']}")
        for b in ("cross_location_eligible", "is_key_holder", "cash_authorised",
                  "food_protection_cert", "is_enrolled", "has_other_employer"):
            if not isinstance(e[b], bool): bad.append(f"{eid}: {b} not bool ({e[b]!r})")
        enrolled = bool(e["is_enrolled"])
        has_cal = bool(e["school_calendar_id"])
        if enrolled != has_cal:
            bad.append(f"{eid}: school_calendar_id must be set iff is_enrolled (enrolled={enrolled}, cal={e['school_calendar_id']!r})")
    add("B. Value domains & enrolment FK", not bad,
        [f"{len(rows)} rows checked, {len(bad)} violation(s)."] + bad[:10])


def check_hours(rows):
    bad = []
    for e in rows:
        if e["min_hours_week"] > e["max_hours_week"]:
            bad.append(f"{e['employee_id']}: min {e['min_hours_week']} > max {e['max_hours_week']}")
        if e["_age"] < 18:
            cap = 18 if e["_age"] <= 15 else 28
            if e["max_hours_week"] > cap:
                bad.append(f"{e['employee_id']} (age {e['_age']}): max {e['max_hours_week']} > legal {cap}")
    add("C. Hours (min<=max, minor weekly caps)", not bad,
        [f"{len(rows)} rows checked, {len(bad)} violation(s)."] + bad[:10])


def check_minor_safety(rows):
    minors = [e for e in rows if e["_age"] < 18]
    kh = [e["employee_id"] for e in minors if e["is_key_holder"]]
    leads = [e["employee_id"] for e in minors if e["role"] == "Shift lead"]
    passed = not kh and not leads
    add("D. Minor safety (no key-holder / shift lead)", passed, [
        f"Minors: {len(minors)}.",
        f"Minors flagged key-holder: {kh or 'none'} (expect none).",
        f"Minors as Shift lead: {leads or 'none'} (expect none).",
    ])


def check_feasibility(rows):
    lines, passed = [], True
    for sid in sorted(STORES):
        crew = [e for e in rows if e["home_location_id"] == sid]
        kh = sum(e["is_key_holder"] for e in crew)
        s3 = sum(e["skill_level"] == 3 and e["role"] == "Barista" for e in crew)
        fpc = sum(e["food_protection_cert"] for e in crew)
        cash = sum(e["cash_authorised"] for e in crew)
        ok = kh >= 3 and s3 >= 2 and fpc >= 4 and cash >= 2
        passed &= ok
        lines.append(f"store {sid}: n={len(crew)} key-holders={kh}(>=3) skill3-barista={s3}(>=2) "
                     f"FPC={fpc}(>=4) cash={cash}(>=2) -> {'OK' if ok else 'FAIL'}")
    add("E. Per-store coverage feasibility", passed, lines)


def check_pii_split(rows):
    lines, passed = [], True
    if not os.path.exists(PII):
        add("F. PII separation", False, [f"Private PII file missing: {PII}"]); return
    pwb = load_workbook(PII, data_only=True)
    pws = pwb["Staff_PII"]
    phdr = [c.value for c in pws[1]]
    pids = {pws.cell(r, 1).value for r in range(2, pws.max_row + 1)}
    tracked_ids = {e["employee_id"] for e in rows}
    cols_ok = set(phdr) == ({"employee_id"} | PII_COLS)
    ids_ok = pids == tracked_ids
    passed = cols_ok and ids_ok
    lines.append(f"Private file columns == employee_id + PII cols: {cols_ok} ({phdr}).")
    lines.append(f"Private file covers all {len(tracked_ids)} tracked employees: {ids_ok} ({len(pids)} rows).")
    add("F. PII separation", passed, lines)


def write_report(sheet_names):
    ok = all(p for _, p, _ in results)
    ts = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "# Roster Scheduling Fields — Validation Report (CS-002 + CS-003)",
        "",
        f"Generated: {ts} by `data/validate_roster_fields.py` (age as-of {AS_OF}).",
        f"Overall: **{'PASS' if ok else 'FAIL'}** ({sum(p for _, p, _ in results)}/{len(results)} checks passed).",
        "",
        f"Sheets in `coffee_shop_staff.xlsx`: {', '.join(sheet_names)}.",
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
    hdr, rows, sheet_names = load_rows()
    check_schema(hdr)
    check_domains(rows)
    check_hours(rows)
    check_minor_safety(rows)
    check_feasibility(rows)
    check_pii_split(rows)
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
