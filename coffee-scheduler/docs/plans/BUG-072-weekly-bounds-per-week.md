# BUG-072 — scope the weekly hour bounds to actual weeks (Plan)

## The defect, precisely

`agents/scheduler/constraints.py` applies the roster's **weekly** hour bounds to the **whole target
window**, whatever its length:

```python
units = [int(slots[index].hours * 2) * var for index, var in employee_vars]
model.Add(sum(units) >= employee.min_hours_week * 2).OnlyEnforceIf(assumptions["shift_rules"])
model.Add(sum(units) <= employee.max_hours_week * 2).OnlyEnforceIf(assumptions["shift_rules"])
```

That is wrong in **both** directions, which is why this is one bug with two faces:

1. **A sub-week window is infeasible by construction.** Most employees carry
   `min_hours_week = 32`, but a 2-day window caps them at 2 x 8 = 16 hours, so the minimum can
   never be met. Observed: a 2-day model is `INFEASIBLE` with `diagnose_infeasibility` naming
   `('legality', 'shift_rules')` — the diagnosis is correct, the constraint is mis-scoped.
2. **A multi-week window is over-constrained.** A 14-day window should allow roughly
   `2 x max_hours_week`, but the current code caps the **whole fortnight** at one week's maximum.
   Nobody has hit this yet only because no window longer than a week has been solved.

## The fix: partition the window into weeks

- **Week definition:** the ISO week, via `date.isocalendar()[:2]` (Monday start). This is stated
  explicitly because the choice is observable, see the edge cases below.
- **Maximum:** apply `sum(hours in week) <= max_hours_week` to **every** week the window touches,
  partial or whole. A ceiling is never wrongly restrictive — a partial week simply cannot exceed a
  full week's allowance — so this is always safe and always correct.
- **Minimum:** apply `sum(hours in week) >= min_hours_week` **only to weeks the window fully
  covers** (all seven dates present). A weekly guarantee is meaningless over a fragment of a week,
  and enforcing it there is exactly what made the model infeasible.
- **Never scale the maximum.** Pro-rating a ceiling downward (`max x days/7`) would forbid legal
  schedules: 16 hours over two days is lawful under a 40-hour week, but a scaled cap of 11.4 would
  reject it. The maximum is applied as written; only the minimum is conditional.
- **Report what was skipped.** Add `skipped_minimums: tuple[tuple[int, tuple[int, int]], ...]` to
  `SchedulingModel`, naming each `(employee_id, (iso_year, iso_week))` whose minimum was not
  applied, so CS-016d can explain it rather than leaving a silent difference in semantics.

## Edge cases this plan commits to covering

- **Window shorter than one week** — no minimum applied anywhere; every touched week still gets
  its maximum; each skipped minimum is recorded. This is the reported symptom.
- **Window spanning several weeks** — the maximum is applied **per week**, so a 14-day window
  allows about twice a 7-day window. A test asserts a 14-day window is *not* capped at one week's
  maximum.
- **A 7-day window that is not Monday-aligned** — e.g. Tuesday to Monday touches *two* ISO weeks
  and fully covers *neither*, so **no minimum is applied**. This is surprising, so it is documented
  and pinned by a test rather than left to be discovered. It is the safe direction: a skipped
  minimum relaxes the model, it never forbids a legal schedule.
- **A Monday-aligned 7-day window** — exactly one fully covered ISO week, so the minimum applies
  once. This is the normal weekly-scheduling case and must behave exactly as the roster intends.
- **`min_hours_week = 0`** — emit no minimum constraint at all, keeping the model smaller rather
  than adding a tautology.
- **`min_hours_week > max_hours_week`** — the `employees` CHECK constraint forbids it, but assert
  it in the builder as defense-in-depth with a typed `SolverModelError` naming the employee, since
  a contradictory pair would otherwise surface as a bare `INFEASIBLE`.
- **An employee with no candidate slot in a fully covered week** — the minimum then demands hours
  the employee cannot legally work, which is a genuine conflict rather than a scoping bug. It is
  out of scope here and is raised as a story (see below), because the right answer is a policy
  decision, not an encoding change.
- **Hours must stay integral** — continue using half-hour units (`int(slot.hours * 2)`) and
  multiply the bounds by 2, so no float enters the model.

## A finding to raise as a story, not to fix here

Making `min_hours_week` a **hard** constraint is questionable regardless of scoping. It is a
guaranteed-hours commitment, and as a hard constraint it can conflict with legality: a minor whose
lawful slots total less than their minimum makes the model infeasible with no legal remedy. The
F11 spec lists shift rules as "min/max length, 30-minute increments, max 5 consecutive days, 10h
rest, no split shifts" — weekly hour bounds are an addition beyond it, and guaranteed hours look
much more like the fairness objective CS-081 describes. This plan keeps the minimum hard but
correctly scoped, and raises the policy question as its own story so the change is deliberate.

## Deliverables

- `agents/scheduler/constraints.py` — per-ISO-week bounds, the full-coverage rule for minimums,
  the defense-in-depth min/max check, and `skipped_minimums` on `SchedulingModel`.
- `test/test_bug072_weekly_bounds.py` — offline tests for each edge case above (sub-week,
  multi-week, aligned and unaligned 7-day windows, zero minimum, contradictory bounds), asserting
  feasibility or the specific binding group, plus the recorded skips.
- Update any CS-016b test that assumed window-total bounds.

## Verification

Offline tests under both runners; `ruff` clean; the four CI scripts; migration round-trip; and the
full suite green. Then confirm the originally reported symptom is gone: a 2-day forward window must
no longer be `INFEASIBLE` for this reason.

## Out of scope

BUG-073, any change to the legality partition, turning `min_hours_week` soft (the story above), and
CS-016d's runner and performance work.
