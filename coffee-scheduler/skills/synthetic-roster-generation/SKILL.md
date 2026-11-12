# Skill: synthetic-roster-generation

Generate realistic, **legal**, solver-feasible synthetic staff rosters for testing and calibration
(`data/build_roster_fields.py` → `coffee_shop_staff.xlsx`; validated by `data/validate_roster_fields.py`).

## Invariants the generator must hold
- **Legal:** no minor is a key-holder or Shift lead; minor weekly `max_hours_week` respects the NY
  school-week caps (≤15yo ≤ 18h, 16–17 ≤ 28h); `min_hours_week ≤ max_hours_week`.
- **Feasible per home store:** ≥3 key-holders, ≥2 skill-3 baristas, ≥4 FPC holders, ≥2 cash-authorised.
- **Deterministic:** `random.seed(SEED)`; age is computed from DOB at a frozen `AS_OF` and **never stored**.
- **PII-safe:** SSN + address split out to `data/private/` (gitignored); tracked file is synthetic only.

## Guardrails (lessons from BUG-001 — see `test/SKILL.md` D1/D6)
The roster partitions into adults vs minors **by DOB at `AS_OF`**. Everything downstream (home-store
quotas, per-store slices) depends on that split. Do **not** hard-code counts that the data owns:
- **Derive the quotas** from the actual adult/minor counts (proportional split with a documented
  remainder rule), or **assert** `sum(adult_quota)+sum(minor_quota) == len(employees)` before writing.
- **Fail closed, descriptively.** If the partition no longer matches the quotas, exit with a message like
  `roster partition 33/7 ≠ quota 32/8 — update allocation` — never let the write loop raise a bare
  `KeyError` mid-file after PII has already been split.
- **Cover everyone.** After assignment, assert every tracked `employee_id` has all scheduling columns
  populated (no blank cells) and every store still meets its feasibility floor.
- **`AS_OF` is a time-bomb.** The split flips when an employee crosses 18 (verified: 32/8 today → 33/7 at
  2028-01-01). Freeze `AS_OF` *with* its dependent constants, and keep a future-date test (D6).
