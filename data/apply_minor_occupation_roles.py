"""BUG-070 — make FED-MINOR-1415-OCCUPATION machine-evaluable.

The rule carried the prose `value_text` 'Restricted equipment/cooking tasks', which no engine
branch could evaluate, so every 14-15 year old's legality raised a config error. The engine now
reads `value_text` as a pipe-separated **allow-list** of roles permitted for the age band; this
script rewrites the workbook cell to that form.

Why `Register` is the only permitted role, mapped onto the existing `stations.station_type` data:

* `Barista` staffs the **Espresso** station — power-driven machinery, which the federal 14-15
  occupation restriction bars.
* `Shift lead` is supervisory and key-holding (opening/closing), also barred for the age band.
* `Register` is cashiering and bagging, which the restriction permits.

No schema change and no new table: the rule row already carries `applies_age_min`/`applies_age_max`
(14/15), `unit = 'ROLE'` and `consequence = 'Block ineligible roles'`. Only the value was unusable.

The rule remains `verification_status = 'VERIFY'`: CS-021 must still confirm the permitted-role set
against the primary source before any demo claims legal accuracy. Implementing the rule does not
verify it.

Idempotent: re-running leaves the workbook unchanged. Read-only on everything but that one cell.

    python3 data/apply_minor_occupation_roles.py [--workbook data/coffee_shop_labor_rules.xlsx]
"""
from __future__ import annotations

import argparse
from pathlib import Path

import openpyxl

RULE_ID = "FED-MINOR-1415-OCCUPATION"
PERMITTED_ROLES = ("Register",)
VALUE_TEXT = "|".join(PERMITTED_ROLES)
NOTE = (
    "Permitted-role allow-list for the 14-15 federal occupation restriction. Barista is barred "
    "(Espresso station = power-driven machinery) and Shift lead is barred (supervisory/key-holder); "
    "Register covers cashiering and bagging. Still VERIFY: CS-021 owns primary-source confirmation."
)


def apply(workbook: Path) -> int:
    book = openpyxl.load_workbook(workbook)
    sheet = book["Labor_Rules"]
    header = [cell.value for cell in next(sheet.iter_rows(min_row=1, max_row=1))]
    col_id = header.index("rule_id") + 1
    col_value = header.index("value_text") + 1
    col_notes = header.index("notes") + 1 if "notes" in header else None

    changed = 0
    for row in range(2, sheet.max_row + 1):
        if sheet.cell(row=row, column=col_id).value != RULE_ID:
            continue
        if sheet.cell(row=row, column=col_value).value != VALUE_TEXT:
            sheet.cell(row=row, column=col_value).value = VALUE_TEXT
            changed += 1
        if col_notes is not None and sheet.cell(row=row, column=col_notes).value != NOTE:
            sheet.cell(row=row, column=col_notes).value = NOTE
            changed += 1
    if not changed:
        print(f"OK — {RULE_ID} already reads {VALUE_TEXT!r}; workbook unchanged.")
        return 0
    book.save(workbook)
    print(f"OK — set {RULE_ID} value_text to {VALUE_TEXT!r} ({changed} cell(s) updated).")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply the BUG-070 permitted-role list.")
    parser.add_argument("--workbook", default=str(Path(__file__).resolve().parent / "coffee_shop_labor_rules.xlsx"))
    args = parser.parse_args(argv)
    return apply(Path(args.workbook))


if __name__ == "__main__":
    raise SystemExit(main())
