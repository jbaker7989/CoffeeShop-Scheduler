"""Add scheduling fields to coffee_shop_staff.xlsx (CS-002 + CS-003).

Deterministic (seeded) generation of legal, solver-feasible synthetic roster fields.
- Removes PII (ssn + address) from the tracked workbook into data/private/coffee_shop_staff_pii.xlsx.
- Appends scheduling columns to the Staff sheet (A-E identity columns kept in place so the
  Summary sheet's positional formulas on Staff!A/D/E still resolve).
- Adds a Data_Dictionary sheet.

Run:  python3 build_roster_fields.py      (needs: openpyxl)
Mirrors the style of build_weather_forecast.py (same directory).

Controlled vocabularies are taken verbatim from coffee_shop_labor_standards.xlsx (Stations,
Coverage_Rules, Fixed_Tasks) and coffee_shop_labor_rules.xlsx (Labor_Rules, School_Calendar).
"""
import os
import random
import datetime as dt
from openpyxl import load_workbook, Workbook
from openpyxl.styles import Font, PatternFill

HERE = os.path.dirname(os.path.abspath(__file__))
STAFF = os.path.join(HERE, "coffee_shop_staff.xlsx")
PRIVDIR = os.path.join(HERE, "private")
PII_OUT = os.path.join(PRIVDIR, "coffee_shop_staff_pii.xlsx")

AS_OF = dt.date(2026, 11, 12)          # age reference for generation (roster never stores age)
SEED = 42
HEADER_FILL = PatternFill("solid", fgColor="5B3A29")
HEADER_FONT = Font(name="Arial", size=10, bold=True, color="FFFFFF")

PII_COLS = ["ssn", "line1-address", "line2-address", "city-address", "state-address", "zipcode-address"]
STORES = [5, 8, 3]                      # location_ids: Lower Manhattan, Hell's Kitchen, Astoria
SCHOOL_CAL = "NYC-DOE-2026-27"
PT_PERSONAS = ["Part-time-mornings", "Part-time-evenings", "Weekends-only"]

# New scheduling columns, in output order (appended after the A-E identity columns).
NEW_COLS = [
    "role", "skill_level", "home_location_id", "cross_location_eligible", "is_key_holder",
    "cash_authorised", "food_protection_cert", "hourly_rate", "availability_persona",
    "min_hours_week", "max_hours_week", "is_enrolled", "school_calendar_id", "grade_band",
    "has_other_employer",
]

DATA_DICTIONARY = [
    ("column", "type", "domain", "source / rule"),
    ("role", "str", "Barista | Register | Shift lead", "Stations.staffed_by_role / Fixed_Tasks.role"),
    ("skill_level", "int", "1-3 (3 = peak-skill eligible)", "COV-PEAK-SKILL (Skill-3 barista)"),
    ("home_location_id", "int", "3 | 5 | 8", "Store_Profile.location_id"),
    ("cross_location_eligible", "bool", "True/False", "cross-store flex (business)"),
    ("is_key_holder", "bool", "True/False (never a minor)", "COV-KEYHOLDER-OPEN/CLOSE"),
    ("cash_authorised", "bool", "True/False", "Register station capability"),
    ("food_protection_cert", "bool", "True/False", "NYC-FPC-COVERAGE (field named by the rule)"),
    ("hourly_rate", "float", "USD/hr (synthetic, not authoritative)", "Cost_Targets (synthetic)"),
    ("availability_persona", "str",
     "Full-time-open | Part-time-mornings | Part-time-evenings | Weekends-only | Student-limited", "availability model"),
    ("min_hours_week", "int", ">=0, <= max_hours_week", "Shift_Rules / contract"),
    ("max_hours_week", "int", "minor caps: 15yo<=18, 16-17<=28", "NY-MINOR-* weekly limits"),
    ("is_enrolled", "bool", "True/False", "CS-003 school enrolment"),
    ("school_calendar_id", "str", "NYC-DOE-2026-27 | '' (blank iff not enrolled)", "School_Calendar / CS-004"),
    ("grade_band", "str", "HS | College | None", "CS-003"),
    ("has_other_employer", "bool", "True/False (default False)", "NY-MINOR-ALL-MULTIEMPLOYER"),
]


def age_on(d):
    return AS_OF.year - d.year - ((AS_OF.month, AS_OF.day) < (d.month, d.day))


def minor_hours(age):
    """Legal weekly min/max for a school-enrolled minor (school-week caps)."""
    if age <= 15:
        return 6, 15          # NY 14-15 school week <= 18
    return 8, 24              # NY 16-17 school week <= 28


def assign_store_adults(adults):
    """adults: list of dicts (sorted by id) for one store. Mutates each with scheduling fields.
    Guarantees per store: >=3 key-holders, >=2 skill-3 baristas, >=4 FPC holders, >=2 cash."""
    for i, e in enumerate(adults):
        e["home_location_id"] = e["_store"]
        e["is_enrolled"] = False
        e["school_calendar_id"] = ""
        e["grade_band"] = "None"
        e["has_other_employer"] = False
        e["cross_location_eligible"] = (i % 3 == 0)
        if i < 3:                                   # 3 shift leads -> key-holders
            e.update(role="Shift lead", skill_level=3, is_key_holder=True, cash_authorised=True,
                     food_protection_cert=True, availability_persona="Full-time-open",
                     hourly_rate=24.0 + i, min_hours_week=32, max_hours_week=40)
        elif i < 5:                                 # 2 skill-3 baristas
            e.update(role="Barista", skill_level=3, is_key_holder=False, cash_authorised=True,
                     food_protection_cert=True, availability_persona="Full-time-open",
                     hourly_rate=20.0, min_hours_week=30, max_hours_week=40)
        else:                                       # remaining adults: part-time mix
            persona = PT_PERSONAS[i % len(PT_PERSONAS)]
            if i % 2 == 0:
                e.update(role="Barista", skill_level=(2 if i % 3 else 1), is_key_holder=False,
                         cash_authorised=True, food_protection_cert=(i % 2 == 0),
                         availability_persona=persona,
                         hourly_rate=18.5 if i % 3 else 17.0, min_hours_week=15, max_hours_week=28)
            else:
                e.update(role="Register", skill_level=1, is_key_holder=False, cash_authorised=True,
                         food_protection_cert=(i % 3 == 0), availability_persona=persona,
                         hourly_rate=17.0, min_hours_week=15, max_hours_week=24)


def assign_minors(minors):
    for i, e in enumerate(minors):
        age = e["_age"]
        lo, hi = minor_hours(age)
        e.update(
            home_location_id=e["_store"],
            role=("Register" if i % 2 else "Barista"),
            skill_level=(2 if age == 17 else 1),
            is_key_holder=False,                     # minors never key-holders
            cash_authorised=True,
            food_protection_cert=False,
            cross_location_eligible=False,
            hourly_rate=16.5,                        # NYC minimum-ish (synthetic)
            availability_persona="Student-limited",
            min_hours_week=lo, max_hours_week=hi,
            is_enrolled=True, school_calendar_id=SCHOOL_CAL, grade_band="HS",
            has_other_employer=(i % 4 == 0),         # 1-2 minors exercise the multi-employer rule
        )


def main():
    random.seed(SEED)
    wb = load_workbook(STAFF)
    ws = wb["Staff"]
    hdr = [c.value for c in ws[1]]
    col = {name: idx + 1 for idx, name in enumerate(hdr)}   # 1-based

    # Read identity + PII per row.
    emps, pii = [], []
    for r in range(2, ws.max_row + 1):
        eid = ws.cell(r, col["employee_id"]).value
        if eid is None:
            continue
        dob = ws.cell(r, col["dob"]).value
        dobd = dob.date() if isinstance(dob, dt.datetime) else dob
        emps.append({"employee_id": eid, "_age": age_on(dobd)})
        pii.append([eid] + [ws.cell(r, col[c]).value for c in PII_COLS])

    # Partition and allocate home stores (bigger stores get more): adults 12/12/8, minors 3/3/2.
    emps.sort(key=lambda e: e["employee_id"])
    adults = [e for e in emps if e["_age"] >= 18]
    minors = [e for e in emps if e["_age"] < 18]
    adult_quota = {5: 12, 8: 12, 3: 8}
    minor_quota = {5: 3, 8: 3, 3: 2}
    for pool, quota, assign in ((adults, adult_quota, assign_store_adults),
                                (minors, minor_quota, assign_minors)):
        by_store, k = {}, 0
        for sid in STORES:
            n = quota[sid]
            for e in pool[k:k + n]:
                e["_store"] = sid
            by_store[sid] = pool[k:k + n]
            k += n
        for sid in STORES:
            assign(by_store[sid])

    fields = {e["employee_id"]: e for e in emps}

    # Rewrite the Staff sheet: drop PII columns, append scheduling columns (keep A-E in place).
    first_pii = min(col[c] for c in PII_COLS)
    ws.delete_cols(first_pii, len(PII_COLS))
    base = ws.max_column
    for j, name in enumerate(NEW_COLS):
        c = ws.cell(1, base + 1 + j, name)
        c.font, c.fill = HEADER_FONT, HEADER_FILL
    for r in range(2, ws.max_row + 1):
        eid = ws.cell(r, col["employee_id"]).value
        e = fields.get(eid)
        if not e:
            continue
        for j, name in enumerate(NEW_COLS):
            ws.cell(r, base + 1 + j, e[name])

    # Data_Dictionary sheet.
    if "Data_Dictionary" in wb.sheetnames:
        del wb["Data_Dictionary"]
    dd = wb.create_sheet("Data_Dictionary")
    for row in DATA_DICTIONARY:
        dd.append(list(row))
    for c in dd[1]:
        c.font, c.fill = HEADER_FONT, HEADER_FILL
    wb.save(STAFF)

    # Private PII workbook (gitignored).
    os.makedirs(PRIVDIR, exist_ok=True)
    pwb = Workbook()
    pws = pwb.active
    pws.title = "Staff_PII"
    pws.append(["employee_id"] + PII_COLS)
    for row in pii:
        pws.append(row)
    pwb.save(PII_OUT)

    print(f"Updated {STAFF}: dropped {len(PII_COLS)} PII cols, added {len(NEW_COLS)} scheduling cols "
          f"for {len(emps)} employees ({len(adults)} adults, {len(minors)} minors).")
    print(f"Wrote private PII -> {PII_OUT}")


if __name__ == "__main__":
    main()
