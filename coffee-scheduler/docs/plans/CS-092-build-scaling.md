# CS-092 — Make the model build scale to a full week (Plan)

## Why: measured, not assumed

`build_model` was profiled with a stub legality (so the cost is purely model construction, not
BUG-073's snapshot loading), on a synthetic 3-store / 40-employee grid:

| Window | Build | Slots | Assignment vars |
|---|---|---|---|
| 1 day | 13.9s | 513 | 20,520 |
| 2 days | 34.6s | 1,026 | 41,040 |
| 3 days | 57.8s | 1,539 | 61,560 |

Superlinear: 3x the slots costs 4.2x the time. `cProfile` on the 3-day build (86s under the
profiler) shows **two** distinct costs, not one:

```
 47,341,179 calls  14.7s  constraints.py:90(_slot_start)
 47,341,179 calls  13.9s  constraints.py:95(_slot_end)
 15,935,192 calls   7.2s  cp_model_helper._add_bounded_linear_expression
 15,935,192 calls   4.5s  cp_model_helper.OnlyEnforceIf
 15,935,192 calls  10.3s  cp_model.add
```

1. **Enumeration.** The minimum-rest rule is a nested scan over `ordered_slots` **inside the
   per-employee loop**, re-deriving each slot's start and end datetime on every comparison. 47.3M
   calls each to `_slot_start`/`_slot_end`, ~28.6s. The gap it computes
   (`_slot_start(right) - _slot_end(left)`) is **employee-independent**, so all of that work is
   repeated 40 times. It also re-slices `ordered_slots[left_position + 1:]` on every outer step.
2. **Constraint count.** Each conflicting pair becomes its own `left + right <= 1` **per
   employee**: ~15.9M constraints for a 3-day window, ~22s just to add them. Even a tiny
   2-day / 1-store / 3-employee fixture produces **19,582 constraints from 486 assignment
   variables** — roughly 40 constraints per variable, almost all of them rest pairs.

**Indexing alone will not fix this.** It removes cost 1 and leaves 15.9M constraints. Cost 2 needs
a different encoding, so this plan does both.

## The fix

### A. Precompute slot times and indices once

Add to `agents/scheduler/shifts.py` a frozen `SlotIndex` plus
`build_slot_index(slots, *, calendar_days, rest)`:

- `start_at: tuple[datetime, ...]`, `end_at: tuple[datetime, ...]` — each slot's tz-aware bounds,
  computed **once**. Nothing may call `_slot_start`/`_slot_end` inside a loop again.
- `by_day: dict[date, tuple[int, ...]]` — for the one-shift-per-day and consecutive-day rules.
- `by_store_day_hour: dict[tuple[int, date, int], tuple[int, ...]]` — built by walking each slot's
  covered hours **once**, instead of rescanning every slot for every `(store, day, hour)`.
- `by_week: dict[tuple[int, int], tuple[int, ...]]` — the ISO-week buckets BUG-072 needs.
- `minutes_from_base: tuple[int, ...]` and `duration_minutes: tuple[int, ...]` — integer time for
  part B, measured from `datetime.combine(min(calendar_days), time.min, ZoneInfo(CANONICAL_TZ))`.

Expose it as `SchedulingModel.index` so `agents/scheduler/objective.py` reuses
`by_store_day_hour` instead of repeating the same scan (the objective currently rebuilds it).

### B. Replace the pairwise rest constraints with one NoOverlap per employee

For each employee and slot, create an **optional interval** starting at `minutes_from_base[i]` with
size `duration_minutes[i] + rest_minutes`, then one `AddNoOverlap` per employee over that
employee's intervals.

Two intervals `[s_l, e_l + rest)` and `[s_r, e_r + rest)` overlap exactly when
`s_r < e_l + rest`, i.e. when `gap < rest` — **precisely** the current pairwise condition. So the
feasible set is unchanged while ~398k constraints per employee collapse to one.

**Preserving the assumption literal (this is the subtle part).** `AddNoOverlap` does not accept
`OnlyEnforceIf`, but F11.ac13's attributable infeasibility depends on every group sitting behind
its literal. So each interval's presence literal is a reified
`active[(employee, slot)] <=> assign[(employee, slot)] AND assumptions["shift_rules"]`, encoded
with three linear constraints (`active <= assign`, `active <= assumption`,
`active >= assign + assumption - 1`). When the assumption is false every interval is absent and
the NoOverlap is vacuous, exactly as `OnlyEnforceIf` behaved. That is 3 constraints per
`(employee, slot)` — about 185k for a 3-day window against 15.9M today.

The existing F11.ac13 test, which asserts `shift_rules` is named when a rest violation makes the
model infeasible, **must keep passing unchanged**. It is the proof the reification preserved
attribution.

### C. Reuse the index everywhere
Coverage rules, station capacity, staff-on-floor, one-shift-per-day, consecutive days, the ISO-week
hour bounds, and the objective all read the index. No loop may rescan `slots`.

## Equivalence is the acceptance criterion, and the baselines already exist

This is a refactor: the model's **meaning must not change**. Golden digests were captured from the
current implementation, fixed seed (`random_seed=1`, `num_search_workers=1`), stub legality, no
revenue prior:

| Fixture | Status | Objective | Slots | Vars | Constraints | Chosen | sha256-16 of chosen |
|---|---|---|---|---|---|---|---|
| 2 days, 1 store, 3 employees | OPTIMAL | **26900** | 162 | 486 | 19,582 | 5 | `90aa2198f9e1cde1` |
| 3 days, 1 store, 4 employees | OPTIMAL | **38300** | 243 | 972 | 39,094 | 7 | `795dee70f5aff209` |

The fixture is one store open 08:00-18:00 every weekday, employees `1..n` with
`hourly_rate = 10 + id`, `min_hours_week = 0`, `max_hours_week = 40`, all coverage rules present at
value `1`, `SR-MIN-LEN 4h`, `SR-MAX-LEN 8h`, `SR-MAX-CONSEC 5d`, `SR-MIN-REST 10h`,
`COV-UTIL-TARGET 1`, one `Coffee` demand record of 2 at 09:00 on 2026-12-07, and
`cost_targets(0.30, 500, 1000, 1000)`.

### Correction: the chosen-assignment hash was the wrong criterion

The hashes above are recorded for history, but **they must not be asserted**, and the plan was
wrong to require them. The objective has **ties**: several assignment sets cost exactly 26900, so
re-encoding the rest rule changes which tied optimum CP-SAT reaches first. The NoOverlap build
reproduces the objective exactly (26900 and 38300) and picks a *different*, equally optimal set.
Asserting the hash would pin an arbitrary tie-break, not the model's meaning.

The equivalence actually asserted is the **whole feasible set** on a fixture small enough to
enumerate — one store open 08:00-13:00, two days, two employees, shifts 4.0-5.0h, coverage minima
0, so the set is shaped purely by the shift rules the encoding touches. Measured on the pairwise
encoding at `ba2a162` and reproduced by the interval encoding, keyed on slot identity rather than
slot index:

```
feasible_solutions = 2401
feasible_set_sha256 = ebb4fb94e397f9bac5b6f86c702876fc563c7d1024b3da8a5c912f31e7eaabf0
```

So the criteria are: **identical feasible set**, **identical objective value**, and a constraint
count that falls by at least 5x (measured 9.5x on these one-store fixtures and about 55x on the
three-store grid; the factor grows with the window, so a 10x floor would fail on the smallest
fixture while understating the real gain).

**Solve time is explicitly not claimed.** Neither encoding can prove optimality on the three-day,
four-employee fixture within 150s — both reach 38300 in under a second and then stall. That is a
pre-existing limitation of the objective, measured on both trees, and it is [[CS-093]], not this
story. Tests therefore cap the solve and assert the objective value, not `OPTIMAL`.

## Edge cases this plan commits to covering

- **DST.** Integer minutes are derived from tz-aware datetimes, so a window spanning a daylight
  transition must still measure real elapsed time. Requires a test over **March 2027** (US DST
  begins 2027-03-14): a 10-hour rest across that boundary must be enforced on true elapsed hours,
  not on naive clock arithmetic. Dec 2026 would never catch this.
- **Non-integer minutes.** Slot bounds are half-hour multiples today, so minute conversion is
  exact; assert exactness and raise `SolverModelError` rather than truncating silently, because
  `int()` on a fractional minute would quietly shorten an interval.
- **`rest = 0`.** Intervals shrink to the slot itself, so NoOverlap forbids only genuine overlap.
  Must not crash and must not forbid adjacent shifts.
- **A single slot, or a single employee.** NoOverlap over one interval is trivially satisfiable; no
  empty-list call into CP-SAT.
- **Two slots with identical bounds.** An employee may hold at most one; `build_slot_grid` never
  emits duplicates, but a caller-supplied grid might.
- **Slots outside the target window** — already refused by the guard added with BUG-072; the index
  must not reintroduce a path that skips it.
- **Empty `by_store_day_hour` bucket** (a closed store-hour) — yields no constraint rather than an
  empty `sum()` that silently reads as 0 against a positive minimum.
- **Determinism.** Every index value is a tuple in sorted order and every dict is iterated in
  sorted key order, so two builds of the same inputs produce byte-identical protos. Assert it.
- **The assumption reification must not leak.** With `shift_rules` assumed false, a rest-violating
  pair must become feasible; with it true, infeasible and attributed. Both directions tested.

## Deliverables (all under `coffee-scheduler/`)

1. `agents/scheduler/shifts.py` — `SlotIndex` and `build_slot_index`.
2. `agents/scheduler/constraints.py` — build the index once, use it throughout, replace the
   pairwise rest constraints with reified optional intervals plus one NoOverlap per employee, and
   expose `index` on `SchedulingModel`.
3. `agents/scheduler/objective.py` — consume `model.index.by_store_day_hour`; delete its own scan.
4. `test/test_cs092_build_scaling.py` — the golden-equivalence tests above, a brute-force
   reference asserting the index matches a naive rescan exactly, the DST test, the rest-semantics
   tests (both assumption directions), determinism, and the edge cases listed.
5. `docs/sdd-cs092.md` — ACs **F11.ac27-ac32**, ids written `**F11.ac27**:` with the **colon
   outside the bold** (the traceability gate regex is `\*\*(F\d+\.ac\d+)\*\*`). F11's AC space is
   used through ac26, so ac27 is the first free id.
6. `Backlog.md` — CS-092 to Done with the measured before/after build times for 1-day, 7-day and
   14-day windows, which is what [[CS-016d]]'s performance budget will be set against.

## Verification

Offline tests under both runners; `ruff` clean; the four CI scripts; migration round-trip; the full
suite green (452 passed today). Then **measure and record**: build time for 1-day, 7-day and
14-day windows, plus the constraint count, against the figures in this plan. CS-092 is only done
when a 14-day window builds, since that is the first window length the old per-window weekly
bounds could not even express.

## Out of scope

Changing any constraint's meaning; solve-time tuning and the runner (CS-016d); the week-definition
question (CS-089); making guaranteed hours soft (CS-088); premium pricing (CS-091).
