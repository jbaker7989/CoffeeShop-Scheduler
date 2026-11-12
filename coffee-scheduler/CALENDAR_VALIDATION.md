# Multi-Year Calendar — Validation Report (CS-004 / CS-007)

Generated: 2026-11-12 00:41:04 by `data/validate_calendars.py` (as-of 2026-11-12).
Overall: **PASS** (4/4 checks passed).

Validates the committed School_Calendar / Holiday_Calendar. The `.mjs` builder/validator require codex's `@oai/artifact-tool` and do not run here; this is the in-repo guard.

## ✅ A. School_Calendar full-date coverage (no gaps/dups/None) — PASS
- Active rows: 2192 (expect 2192 = every day 2023-01-01..2028-12-31).
- Missing dates: 0.
- Duplicate active dates: 0.
- Rows outside range: 0.

## ✅ B. School_Calendar invariants (weekend/assumption/day-before) — PASS
- Weekend rows marked in_session: 0 (expect 0).
- is_assumption=True outside the 2027-28 planning term: 0 (expect 0).
- day_before_school_day != next-day in_session: 0 (expect 0).

## ✅ C. Roster FK resolves (NYC-DOE-2026-27) — PASS
- calendar_id 'NYC-DOE-2026-27' present: True.
- Its date span: 2026-09-10..2027-06-28.
- Every date in the 2026-27 term 2026-09-10..2027-06-28 carries that id: True.

## ✅ D. Holiday_Calendar (federal + Election Day) — PASS
- US-FED rows: 66 (expect 66 = 11/yr x 6).
- Missing federal holidays: 0.
- Years missing a correct Election Day (first Tue after first Mon): none.
- [info] Federal dates also emitted as NY-STATE (dual-jurisdiction duplication): 66 (by design; match holidays by date).
