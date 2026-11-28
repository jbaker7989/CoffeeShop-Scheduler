# CS-088 + CS-089 — Soft guaranteed hours, and one authoritative workweek (Plan)

Two coupled F11 solver-correctness stories, developed in sequence on one branch. CS-088 makes a
guaranteed-hours shortfall a *soft* penalty; CS-089 then makes the week that shortfall (and every
other week-relative rule) is scoped to a single authoritative definition. CS-088 lands first using
the existing ISO-week scoping; CS-089 unifies all sites onto one shared helper.

---

# Part A — CS-088: make guaranteed weekly hours a soft fairness term

## Why

`build_model` enforces `min_hours_week` as a **hard** CP-SAT constraint
(`constraints.py:312`: `sum(units) >= min_hours_week * 2` behind `assumptions["shift_rules"]`).
That is wrong in a way that bites real rosters: a minor whose **lawful** hours — after the
child-labour caps already encoded — total fewer than their guaranteed minimum makes the whole model
**infeasible, with no legal remedy**. The only way to satisfy the hard constraint would be to
schedule them illegally. A guaranteed-hours commitment is a *fairness/contractual* goal, not one of
F11's stated shift rules ("min/max length, 30-minute increments, max 5 consecutive days, 10h rest,
no split shifts"), so it belongs in the objective, not the hard model.

## The change

### `agents/scheduler/constraints.py`
- **Remove** the hard minimum (the `if employee.min_hours_week == 0 / len(week_days) == 7 /
  sum(units) >= min_hours_week*2` block, `constraints.py:309-314`). **Keep `max_hours_week` hard** —
  a ceiling can always be met by scheduling less, so it never causes an unlawful-but-forced
  schedule.
- **Keep** the `min_hours_week > max_hours_week` build-time raise: with a soft minimum this is no
  longer an infeasibility, but it is still nonsensical data (a guarantee a hard ceiling forbids), so
  it stays a fail-closed config error — it does **not** fire for the minor case above (there
  `min < max`; the conflict is with the law, not the ceiling).
- **Expose the fully-covered weeks** the soft term needs. Add
  `full_weeks: tuple[tuple[int, int], ...]` to `SchedulingModel` — the ISO `(year, week)` keys the
  target window **fully** covers (all 7 days present), computed from the `days_by_week` partition
  already built. The guarantee applies only to fully-covered weeks, exactly as BUG-072 scoped it; a
  partial week at a window edge still imposes no guarantee. Keep `skipped_minimums` (partial weeks
  per employee with a positive minimum) for continuity.

### `agents/scheduler/objective.py`
- Add `ObjectiveWeights.guaranteed_hours_shortfall: int = 2` with a one-line rationale (below), and
  validate it in the existing non-negative-integer weight check.
- Add a term, mirroring the **overtime** pricing pattern (which also prices half-hours in cents at
  the rate): for each employee with `min_hours_week > 0` and each week in `model.full_weeks`,
  - `units_week = Σ int(slot.hours*2) * assign[(emp, idx)]` over `index.by_week[(year, week)]`,
  - `shortfall = NewIntVar(0, min_hours_week*2)`, `Add(shortfall >= min_hours_week*2 - units_week)`,
  - price each short half-hour **in cents at the employee's rate**:
    `_cents(rate / 2) * shortfall` — commensurate with `labour_cost`, so the solver trades the two
    in real dollars rather than across unit systems.
- Add `guaranteed_hours_shortfall: Any` and `guaranteed_hours_slack: dict[tuple[int, tuple[int,int]],
  cp_model.IntVar]` to `ObjectiveTerms`, and `+ weights.guaranteed_hours_shortfall *
  terms.guaranteed_hours_shortfall` to the `Minimize`.

### Default weight rationale (document in the SDD)
Pricing the shortfall at the rate makes one short half-hour cost `_cents(rate/2)`, the same as the
labour of filling it. A weight of **2** therefore makes the solver **weakly prefer honouring a
guarantee** (filling a half-hour nets `-_cents(rate/2)`), while coverage (100) and overtime (10)
still dominate, so a guarantee is **never** honoured at the expense of a coverage minimum, an
overtime budget, or any hard legal rule. The weight is tunable; the ratified production value can be
revisited like the CS-014b acceptance band.

## Softness is the point — and must be tested
No value of the term may make a feasible model infeasible: `shortfall` is a non-negative slack that
is *penalised*, never forced to zero. A minor whose lawful hours fall short of their guarantee now
yields a *feasible* schedule with a shortfall penalty, where today the model is infeasible.

## CS-088 edge cases the tests must cover
- **The minor case (the reason this story exists)**: an employee whose legal/coverage constraints
  cap them below `min_hours_week` → the model is **feasible** (was infeasible), with a shortfall
  penalty recorded on the slack. A test builds exactly this and asserts feasibility + a positive
  shortfall.
- **Direction**: raising `guaranteed_hours_shortfall` from 0 to a large value increases the hours an
  under-booked guaranteed employee receives (a behavioural assertion, not a pinned schedule).
- **`min_hours_week == 0`**: no slack, no term for that employee.
- **Partial week**: a window that does not fully cover a week imposes no guarantee and no shortfall
  for that week (still recorded in `skipped_minimums`).
- **`min > max`**: still raises at build time (config error), unchanged.
- **Fully met**: an employee scheduled at or above their guarantee contributes zero shortfall.
- **Max stays hard**: a model forced above `max_hours_week` is infeasible and attributed to
  `shift_rules` (unchanged).

---

# Part B — CS-089: one authoritative definition of "the week"

## Why

Several week-relative notions are now in play and **nothing reconciles them**:
1. BUG-072's weekly hour bounds use the **ISO week** (`day.isocalendar()[:2]`).
2. `legality_from_compliance` passes `week_start=inputs.target_window[0]` (`constraints.py:417`) —
   the window's first day, which is only Monday by accident.
3. **Overtime** (`objective.py:211-212`) sums `units` over the **whole window** and compares to
   `_HALF_HOURS_PER_WEEK = 80`, so on a two-week window overtime triggers at 40h **total**, not 40h
   **per week** — a real bug for any window longer than a week.
4. Payroll's week is a documented-but-unenforced fourth notion.

Minor hour caps, overtime, and now CS-088's guarantee are all week-relative, so a split definition
can mis-apply a cap at a week boundary.

## The change

### A single shared helper
Add `workweek(day: date) -> tuple[int, int]` (and `workweek_days(window) -> dict[...]`) to
`agents/scheduler/shifts.py`, returning the **ISO `(year, week)`** — Monday-start, the established
7-day workweek. One function, documented as *the* workweek for hour caps, overtime and guarantees,
with payroll defined to use the same boundary. All sites below call it; none re-derives a week.

### Align every week-relative site
1. **BUG-072 hour bounds** (`build_model`): replace the inline `day.isocalendar()` partition with
   `workweek`/`workweek_days`. Behaviour unchanged (already ISO) — this removes the second copy.
2. **CS-088 full weeks / shortfall**: scope to `workweek` (same helper), so Part A and the hour
   bounds cannot drift.
3. **`legality_from_compliance`** (`constraints.py:417`): set `week_start` to the **Monday of the
   slot's ISO workweek**, not `inputs.target_window[0]`. The per-assignment metrics evaluated here
   are day-scoped (`hours_per_day`, `span_hours`) so the verdict does not change today, but the
   field becomes correct for when a week-scoped metric is added, and stops implying the window start
   is the week start.
4. **Overtime** (`objective.py`): compute overtime **per workweek** — `units_week` per ISO week,
   `slack_week >= units_week - 80`, summed — instead of one window-wide `units` vs 80. This
   **changes the objective for multi-week (and non-Monday-aligned single-week) windows**; it is a
   **bug fix**, and the SDD states the before/after explicitly. Single Monday-aligned week:
   unchanged.

### Document the one definition
`docs/sdd-cs089.md` states: the authoritative workweek is the ISO week (Monday 00:00 to Sunday
24:00, local `America/New_York`); hour caps, guaranteed-hours shortfall, overtime and payroll all
use it; `legality_from_compliance.week_start` is its Monday. Note that a *configurable* employer
workweek (e.g. a Sun–Sat establishment week) is a future extension, explicitly out of scope, and
would change only the helper.

## CS-089 edge cases the tests must cover
- **Single source**: BUG-072 bounds, CS-088 full weeks, and overtime all derive their week from
  `workweek`; a test asserts the helper's output for a known boundary (e.g. 2026-12-07 is a Monday →
  one ISO week; a Sunday falls in the prior ISO week).
- **Overtime per week, not per window**: a two-week window where an employee works 30h each week
  (60h total, under 80 total but each week under 40) incurs **zero** overtime, where the old
  whole-window code charged overtime on the 60h−40h = 20h excess. A test pins this.
- **Non-Monday-aligned week**: a 7-day window starting on a Wednesday spans two ISO weeks; caps and
  overtime apply per ISO week, and `legality_from_compliance.week_start` is each slot's Monday, not
  the Wednesday.
- **Compliance week_start**: a slot's proposal carries the Monday of its ISO week; a test asserts it
  for a mid-week slot.
- **No behavioural change on the common case**: a single Monday–Sunday week produces the same
  objective and feasibility as before (overtime, bounds, guarantee all identical).

---

## Deliverables (all under `coffee-scheduler/`)

1. `agents/scheduler/shifts.py` — `workweek` / `workweek_days` helper (CS-089).
2. `agents/scheduler/constraints.py` — CS-088 (remove hard min, keep max, add `full_weeks`) and
   CS-089 (use `workweek`, fix `week_start`).
3. `agents/scheduler/objective.py` — CS-088 (shortfall term + weight) and CS-089 (per-week overtime).
4. `test/test_cs088_guaranteed_hours.py`, `test/test_cs089_workweek.py` — offline, stub legality,
   capped solves; every edge case above; each test tagged `# spec: F11.acN`.
5. `docs/sdd-cs088.md` (F11.ac60 onward) and `docs/sdd-cs089.md` (continuing), ids written
   `**F11.ac60**:` with the colon **outside** the bold (gate regex `\*\*(F\d+\.ac\d+)\*\*`); ac59 is
   the current top.
6. `FEATURES.md` — `- **CS-088 (soft guaranteed hours, done):** …` and
   `- **CS-089 (one workweek, done):** …` bullets under `### F11`.
7. `Backlog.md` — CS-088 and CS-089 to `✅ Done (2026-11-24)`; leave every other entry untouched.

## Verification (Claude)

Offline tests under **both** runners; `ruff` clean on changed files; the full suite green against
live Postgres; the four CI gate scripts; the migration round-trip (reseed after). Prove CS-088
bites: build the minor-shortfall fixture and confirm it was **infeasible before** the change and
**feasible with a penalty after**. Prove CS-089's overtime fix: the two-week fixture charges
**per-week**, not per-window. Keep every solve capped (small `SolverStopPolicy`) and assert
objective values / feasibility, never an `OPTIMAL` status.

## Out of scope

A configurable per-employer workweek (ISO is fixed here); enforcing minor **weekly-hour** caps in
the solver (those are schedule-scoped and excluded from `PER_ASSIGNMENT_METRICS` by BUG-071 — a
separate story); strengthening the optimality bound ([[CS-097]]); compliance session reuse
([[CS-090]]); any change to a coverage or legality rule's meaning.
