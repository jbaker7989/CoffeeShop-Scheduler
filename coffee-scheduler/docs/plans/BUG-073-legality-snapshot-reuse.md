# BUG-073 — reuse the compliance snapshot per employee (Plan)

## The defect, precisely

`agents/scheduler/constraints.py::legality_from_compliance` memoises its verdict on
`(employee_id, location_id, day, start, end)` and calls `compliance.load_snapshot(proposal)`
**inside** that function. So one snapshot is loaded per unique candidate span.

`compliance.engine.load_snapshot` opens a **brand-new psycopg connection** per call
(`psycopg.connect(url)`), runs `prepare_session`, then `_read_snapshot` issues roughly seven
queries. For a week of 3 stores x 40 employees x every candidate span that is tens of thousands of
connections. Measured: a 2-day window builds in seconds; a full 7-day window did not finish in
**over 12 minutes** and had to be cancelled. It is also why CI's Test suite went from ~7 minutes to
**14m35s**.

## Why the fix is provably safe (read `_read_snapshot` before doubting this)

`_read_snapshot(conn, proposal)` touches the proposal **only** through three fields:

| Field | Used for |
|---|---|
| `proposal.tenant_id` | the `tenants` row, and `prepare_session`'s RLS tenant GUC |
| `proposal.employer_id` | filtering `employer_config` (the `fww_covered` flag) |
| `proposal.employee_id` | the `employees` row, `consent_waivers`, and the school calendar |

Everything else is **unfiltered**: `labor_rules` is read whole, `consent_waivers` is filtered by
employee only, `school_calendar_days` is read **for the employee's entire calendar with no date
filter**, `holidays` is queried only for Labor Day, and `wage_floor` is derived from the rules.

So the snapshot contains **no date, location or span-specific data**. Caching it per
`(tenant_id, employer_id, employee_id)` returns exactly the object the current code would have
built for every candidate of that employee. For 40 employees that is **40 snapshot loads instead
of tens of thousands**, with no change in any verdict.

## The change

In `legality_from_compliance`:

1. Add an inner, **closure-scoped** cache `snapshot_for(tenant_id, employer_id, employee_id)`
   wrapping `load_snapshot`. It must live inside `legality_from_compliance`, **not** at module
   level: a module-level cache would serve a snapshot from one tenant or DSN to a later call with
   a different tenant or DSN. This is the main correctness trap in this fix.
2. Build the `PER_ASSIGNMENT_METRICS` rule filter **once per employee** rather than per candidate,
   since it depends only on the snapshot.
3. Keep the per-candidate verdict memo exactly as it is — the verdict genuinely depends on the
   span and day.
4. Do not mutate the cached snapshot. The existing `replace(snapshot, rules=decidable)` already
   returns a copy, so the cached object is untouched; keep it that way. `days` and `waivers` are
   shared references, so nothing may mutate them either.

## Edge cases this plan commits to covering

- **Different employees must not share a snapshot** — the key includes `employee_id`; a test
  asserts two employees get different `Employee` rows and different waivers.
- **Different tenants or DSNs must not share a snapshot** — the cache is closure-scoped, so a
  second `legality_from_compliance` call gets its own. A test builds two adapters and asserts the
  cache is not shared.
- **`employer_id` must be in the key** — it selects the `employer_config` row and therefore the
  `fww_covered` flag. Omitting it would silently apply one employer's Fair Workweek status to
  another.
- **The same employee across different days and locations must reuse one snapshot** — this is the
  whole point; a test counts `load_snapshot` calls and asserts it equals the number of distinct
  employees, not the number of candidates.
- **Verdicts must be unchanged** — a test asserts the verdict for a given candidate is identical
  before and after reuse, so the optimisation cannot quietly alter legality.
- **A future change to `_read_snapshot` must not break the assumption.** This is the real risk: if
  someone adds a date filter to the school-calendar or rules query, per-employee caching becomes
  wrong. **Guard it with a test that loads two snapshots for the same employee on different days
  and at different locations and asserts they are equal**, so the assumption is enforced by CI
  rather than by a comment.

## Deliverables

- `agents/scheduler/constraints.py` — the closure-scoped snapshot cache and the per-employee rule
  filter. No change to any verdict, constraint or public signature.
- `test/test_bug073_snapshot_reuse.py` — offline tests with a stub snapshot loader counting calls,
  plus one DB-gated rollback-only test asserting (a) a snapshot is loaded once per employee and
  (b) a given candidate's verdict is byte-identical to the un-cached result.
- Record the measured before/after model-build time for a 2-day and a 7-day window in `Backlog.md`.

## Verification

Offline tests under both runners; the DB-gated test against live Postgres; `ruff` clean; the four
CI scripts; migration round-trip. Then **measure**: build the model for a full 7-day forward week
and record the wall-clock time. BUG-073 is only fixed if a full week now builds, which is also the
precondition for assessing CS-016d's performance target.

## Out of scope

Narrowing the candidate span grid, any change to `compliance/engine.py`, and BUG-072.
