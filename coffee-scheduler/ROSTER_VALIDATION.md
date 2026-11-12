# Roster Scheduling Fields — Validation Report (CS-002 + CS-003)

Generated: 2026-11-12 00:06:51 by `data/validate_roster_fields.py` (age as-of 2026-11-12).
Overall: **PASS** (6/6 checks passed).

Sheets in `coffee_shop_staff.xlsx`: Staff, Summary, Data_Dictionary.

## ✅ A. Schema & column layout — PASS
- Missing columns: none.
- PII columns still in tracked file: none (expect none).
- Identity cols A-E preserved in place: yes.

## ✅ B. Value domains & enrolment FK — PASS
- 40 rows checked, 0 violation(s).

## ✅ C. Hours (min<=max, minor weekly caps) — PASS
- 40 rows checked, 0 violation(s).

## ✅ D. Minor safety (no key-holder / shift lead) — PASS
- Minors: 8.
- Minors flagged key-holder: none (expect none).
- Minors as Shift lead: none (expect none).

## ✅ E. Per-store coverage feasibility — PASS
- store 3: n=10 key-holders=3(>=3) skill3-barista=2(>=2) FPC=6(>=4) cash=10(>=2) -> OK
- store 5: n=15 key-holders=3(>=3) skill3-barista=2(>=2) FPC=9(>=4) cash=15(>=2) -> OK
- store 8: n=15 key-holders=3(>=3) skill3-barista=2(>=2) FPC=9(>=4) cash=15(>=2) -> OK

## ✅ F. PII separation — PASS
- Private file columns == employee_id + PII cols: True (['employee_id', 'ssn', 'line1-address', 'line2-address', 'city-address', 'state-address', 'zipcode-address']).
- Private file covers all 40 tracked employees: True (40 rows).
