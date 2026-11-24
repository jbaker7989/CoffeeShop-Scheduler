# CS-094 — Require coverage for every open store-hour (Plan)

## The defect, demonstrated

`build_model`'s coverage loop is driven by where **slots exist**, not by where the **store is
open**. Two lines cause it:

```python
store_days = sorted({day for location_id, day, _hour in index.by_store_day_hour if location_id == store_id})
...
    for hour in range(window.open_hour, window.close_hour):
        slot_indices = index.by_store_day_hour.get((store_id, day, hour), ())
        vars_here = [...]
        if not vars_here:
            continue          # <-- an OPEN hour with no covering slot gets no minimum
```

1. `store_days` is taken from `index.by_store_day_hour`, whose keys only exist for `(store, day,
   hour)` triples that **some slot covers**. A store-day with **zero** candidate slots is absent
   from that set, so the whole day is skipped and none of its coverage minima are enforced.
2. Even on a processed day, `if not vars_here: continue` drops the minima for any individual open
   hour no slot covers.

Both are the same fail-open: **coverage is required only where staffing is already possible.**

### Reproduced (holds on `163f764`)

A store open 08:00-10:00 with a 4-hour minimum shift produces **zero** slots (`build_slot_grid`
needs `start + minimum <= closing`, and `10 - 8 < 4`). `COV-MIN-CREW` is seeded at **2**. The
solver reports the model **satisfiable with the store open and nobody scheduled**, instead of
reporting that those hours cannot be staffed. This was measured during the CS-092 review on both
the old and the new encoding.

## Design decision: structurally uncoverable open hour → build-time error, not infeasibility

There are two distinct ways an open hour can fail to be covered, and they must be handled
differently:

- **Zero slots cover the hour** (the grid is too coarse, or the window is unstaffable, e.g. open
  08:00-10:00 with a 4h minimum). **No assignment choice can ever fix this** — it is a data /
  configuration error. The fix raises a typed `SolverModelError` at build time naming the store,
  day, hour and the coverage group whose minimum is unmet. This is strictly better than emitting
  `sum([]) >= 2` and letting the model go infeasible, because it needs no solve, it names the exact
  hour (CP-SAT's assumption core would name only the group), and it frames the cause correctly.
  Raising here is consistent with `build_model`'s existing contract: it already raises
  `SolverModelError` for an unknown store, a stray slot outside the window, and `min > max` hours.
- **Slots cover the hour but not enough of the right role or count** (candidates exist, just not a
  key-holder, or fewer than the minimum). This is genuine **scheduling infeasibility** and is
  already handled correctly: the minimum constraint is emitted under its assumption, the model is
  infeasible, and `diagnose_infeasibility` attributes it to that coverage group. **This path does
  not change.**

So the raise fires on exactly one condition: **an open store-hour that no candidate slot covers,
while a minimum coverage requirement is in force for it.** The `<=` capacity constraints
(`station_capacity`) are never a reason to raise — an empty hour satisfies `sum([]) <= stations`
trivially, so they are correctly skipped when empty.

## The "open store-hour" definition must match `build_slot_grid` exactly

`build_slot_grid` treats a store-day as open iff:

```python
window is not None and day.weekday() in window.open_weekdays
```

The coverage loop today checks only `window is not None`, ignoring `open_weekdays`. If the two
disagree, the new build-time raise would fire on a day `build_slot_grid` never generated slots for
— a **false** error. The fix must use the **same** open-day predicate as `build_slot_grid` in the
coverage loop. This is the single most dangerous subtlety in the change.

## The fix

In `agents/scheduler/constraints.py`, inside `build_model`:

1. **Drive the store-day loop from the target window, not the index.** Replace the index-derived
   `store_days` with the `calendar_days` already computed in `build_model`, filtered to the store's
   open days using the **exact** `build_slot_grid` predicate (`window is not None and
   day.weekday() in window.open_weekdays`).
2. **Validate every window actually used:** refuse `close_hour <= open_hour` with a typed
   `SolverModelError` naming the store and weekday. Nothing validates this today, so a degenerate
   window is representable; the key-holder loop would otherwise demand staff at an hour the store
   is closed. (No seeded row is degenerate, so this is latent, not live. A contract-level
   `StoreOpenWindow.__post_init__` check was considered but rejected: it would touch the forecast
   read path in `contracts/forecast.py`, widening the blast radius; the validation is kept
   scheduler-local.)
3. **For each open hour, if no candidate slot covers it, decide between raise and skip by rule
   direction:**
   - Compute `covering = index.by_store_day_hour.get((store_id, day, hour), ())` once.
   - If `covering` is empty **and** any applicable minimum coverage rule is in force for that hour
     (`min_crew`, non-FLAG `break_relief`, `fpc`, `key_holder` at open/close, `peak_skill` at a
     peak hour) with a positive required value, **raise** `SolverModelError` naming
     `store / day / hour / rule_id`.
   - If `covering` is non-empty, emit the existing constraints unchanged.
   - The `<=` capacity constraints on an empty hour are simply not emitted (nothing to bound).
4. Keep every emitted constraint, its assumption group, and its numeric value **exactly as today**.
   This story changes **which hours are checked**, never **how** a checked hour is constrained.

## Edge cases the tests must cover

- **Whole-day-empty** (the live repro): store open 08:00-10:00, 4h minimum, `COV-MIN-CREW = 2` →
  `build_model` raises naming store, a date, hour 8 and `COV-MIN-CREW`. Today it returns a
  satisfiable model.
- **Partial-day gap** with a caller-supplied sparse `slots=` grid: a day with slots covering some
  but not all open hours → raises for the uncovered open hour, builds for the covered ones.
- **Open-weekday mismatch**: a `store.open_windows` entry exists for a weekday that is **not** in
  that window's `open_weekdays`. `build_slot_grid` emits no slots for it; the coverage loop must
  **also** treat it as closed and **not** raise.
- **Degenerate window** `close_hour <= open_hour` → raises at build time, naming the store/weekday,
  before any hour loop.
- **Sub-hour slot**: a caller-supplied `[08:00, 08:30]` slot covers no integer hour (it lands in
  `by_day` but no `by_store_day_hour` bucket), so an open hour whose only slot is sub-hour is still
  uncovered → raises. Confirms a sub-hour slot cannot satisfy a coverage minimum.
- **Role-shortfall is NOT a build error**: slots cover the hour but none is a key-holder → the
  model stays **infeasible and attributed to `coverage_key_holder`**, exactly as today. A test
  asserts the build does **not** raise and the infeasibility is diagnosed.
- **The happy path is unchanged**: the seeded 1-day and 7-day builds (06:00-21:00, 4h min) still
  build with no raise, identical assignments/objective, because every open hour is coverable.

## Deliverables (all under `coffee-scheduler/`)

1. `agents/scheduler/constraints.py` — the coverage-loop changes above. No change to any
   constraint's meaning, value or assumption group.
2. `test/test_cs094_open_hour_coverage.py` — `unittest`, offline, stub legality, covering every
   edge case above, each tagged `# spec: F11.acN`. Includes a regression test that the exact live
   repro now raises, and a happy-path test that a fully-coverable window still builds unchanged.
3. `docs/sdd-cs094.md` — ACs **F11.ac34-ac3X** with the AC→test table. Write ids as
   `**F11.ac34**:` with the colon **outside** the bold (the traceability gate regex is
   `\*\*(F\d+\.ac\d+)\*\*`). F11's AC space is used through ac33.
4. `FEATURES.md` — a short `- **CS-094 (open-hour coverage, done):** …` bullet under `### F11`.
   Do not change F11's status; never use etc./TBD/TODO.
5. `Backlog.md` — CS-094 to `✅ Done (2026-11-23)` with a one-paragraph summary. Leave every other
   entry untouched.

## Verification (Claude)

Offline tests under **both** runners (`python3 -m unittest discover -s test` and
`python3 -m pytest test/`); `ruff check --config ruff.toml` clean on changed files; the full suite
green against live Postgres; the four CI gate scripts; the migration round-trip. Then **prove the
guard bites**: confirm the live repro raises after the fix and returned a satisfiable model before
it, and confirm the seeded 7-day build is unchanged (same objective, no raise).

## Out of scope

Changing `build_slot_grid` to emit finer slots for narrow windows (that would *hide* uncoverable
windows rather than report them); the solver stopping rule ([[CS-093]]); the coverage-rule
free-text matcher `_coverage_rules` (a separate latent issue); any change to a constraint's numeric
value or assumption group.
