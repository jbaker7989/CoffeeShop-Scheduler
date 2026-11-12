"""Encode the CS-005 closing-hours decision into coffee_shop_labor_standards.xlsx.

Decision (suggested set, approved 2026-11-12):
- Lower Manhattan (5) & Hell's Kitchen (8): open 06:00, close 21:00.
- Astoria (3): open 07:00 (data fix), close 20:30.
- No dead-hour evening crew (Q4=C) -> no new Coverage_Rules row.

Updates Store_Profile open/close (first_staff_in / last_staff_out are formulas that auto-derive
from open/close + pre/post minutes), recomputes Hours_Validation.hours_after_last_txn_to_close
against the new closes, and refreshes the Finding note. Idempotent.

Run:  python3 apply_closing_hours.py      (needs: openpyxl)
"""
import os
import datetime as dt
from openpyxl import load_workbook

HERE = os.path.dirname(os.path.abspath(__file__))
STD = os.path.join(HERE, "coffee_shop_labor_standards.xlsx")

OPEN = {5: dt.time(6, 0), 8: dt.time(6, 0), 3: dt.time(7, 0)}
CLOSE = {5: dt.time(21, 0), 8: dt.time(21, 0), 3: dt.time(20, 30)}
FINDING = ("CS-005 resolved 2026-11-12: close set to 21:00 for Lower Manhattan & Hell's Kitchen "
           "and 20:30 for Astoria (last-tx + ~30 min); Astoria open corrected to 07:00 (data fix). "
           "Dead-hour evening crew eliminated (Q4=C). hours_after_last_txn_to_close recomputed "
           "against the new close times. See coffee-scheduler/docs/decisions/CS-005-closing-hours.md.")


def main():
    wb = load_workbook(STD)

    sp = wb["Store_Profile"]
    hdr = {c.value: i + 1 for i, c in enumerate(sp[1])}
    n_hours = 0
    for r in range(2, sp.max_row + 1):
        loc = sp.cell(r, hdr["location_id"]).value
        if loc in OPEN:
            sp.cell(r, hdr["open_time"]).value = OPEN[loc]
            sp.cell(r, hdr["close_time"]).value = CLOSE[loc]
            n_hours += 1

    hv = wb["Hours_Validation"]
    hh = {c.value: i + 1 for i, c in enumerate(hv[1])}
    n_recomp = 0
    for r in range(2, hv.max_row + 1):
        loc = hv.cell(r, hh["location_id"]).value
        last = hv.cell(r, hh["last_transaction"]).value
        if loc in CLOSE and isinstance(last, dt.time):
            delta = (dt.datetime.combine(dt.date(2000, 1, 1), CLOSE[loc])
                     - dt.datetime.combine(dt.date(2000, 1, 1), last))
            hv.cell(r, hh["hours_after_last_txn_to_close"]).value = round(delta.total_seconds() / 3600, 2)
            n_recomp += 1
    # Refresh the free-text Finding note (col B of the row whose col A == 'Finding').
    for r in range(2, hv.max_row + 1):
        if hv.cell(r, 1).value == "Finding":
            hv.cell(r, 2).value = FINDING
            break

    wb.save(STD)
    print(f"Store_Profile: set open/close on {n_hours} rows. "
          f"Hours_Validation: recomputed {n_recomp} hours-to-close values + Finding note.")


if __name__ == "__main__":
    main()
