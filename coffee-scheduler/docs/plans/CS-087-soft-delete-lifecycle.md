# CS-087 — Soft-delete data lifecycle (no hard deletes) — development plan

> Feature: F69 (data governance / retention) · Priority: P1 · Size: M
> Implements the **ABSOLUTE soft-delete policy** added to `migration/SKILL.md` (2026-11-17).

## Context
The project just adopted an absolute rule in `coffee-scheduler/migration/SKILL.md`: **domain data is never
hard-deleted — "delete" is a state change, never a row removal.** Today nothing enforces that. Most domain
tables have no soft-delete marker, the only lifecycle markers that exist are ad-hoc (`tenants.status`,
`tenant_invitations.status`, `tenant_users.is_active`, `events.status`), every domain table FKs
`tenants(id) ON DELETE CASCADE`, and there is no guard stopping a future migration or repository from issuing
`DELETE FROM <domain table>`. CS-087 makes the policy real and mechanically enforced, with the smallest,
most-verifiable surface that still covers every editable business record.

**Why now:** the policy is written but unenforced; CS-062/063 (onboarding, per-tenant config) and the manager
screens (CS-031–035) are about to add the first real "delete this store / employee / rule" actions, so the
mechanism must exist before those land and bake a hard `DELETE` into the app.

## Scope decisions (locked — minimize bugs)
1. **Reads are excluded at the database, not by convention.** A per-table **`AS RESTRICTIVE FOR SELECT`** RLS
   policy hides soft-deleted rows from the app automatically, so no query, view, or future screen can forget
   to filter. (The existing permissive `tenant_isolation` `FOR ALL` policy is untouched; a restrictive policy
   AND-combines with it, and only for `SELECT`.)
2. **Scope = editable domain records only.** Append-only logs, immutable ingested source data, security-token
   infra, and tables that already carry a lifecycle marker are **excluded** (classification below). This keeps
   the retrofit clear of the riskiest schema features (partitioning, temporal `EXCLUDE`, append-only grants).
3. **FK `ON DELETE CASCADE → RESTRICT` swap is explicitly DEFERRED** to a separate story. Under the
   no-hard-delete rule a parent is never hard-deleted, so `CASCADE` is dormant; and the F69 tenant-erasure
   purge path *intentionally* relies on `tenants … ON DELETE CASCADE` to wipe a tenant in one audited step.
   Swapping ~40 FKs now is the highest-risk, lowest-value part of the retrofit. It is recorded as a decision,
   not built here.
4. **The only hard-delete that remains lawful** is the audited **F69 GDPR erasure** path (employee PII
   right-to-be-forgotten, whole-tenant purge). CS-087 does not build F69; it leaves that exception intact and
   documents it.

## Table classification (the whole schema)
**Tier A — add `deleted_at` + `deleted_by` + partial index + restrictive SELECT policy** (editable records):
`employer_config, stores, store_hours, stations, fixed_tasks, coverage_rules, shift_rules, cost_targets,
throughput_standards, demand_adjustments, product_taxonomy, taxonomy_meta, employees, employee_pii,
labor_rules, consent_waivers, school_calendars, school_calendar_days, holidays, schedules, shifts,
shift_assignments, forecasts`.

**Tier B — already has a lifecycle marker (document only, no DDL):** `tenants` (`status`),
`tenant_invitations` (`status`), `tenant_users` (`is_active` + `sessions_revoked_at`), `events` (`status`,
"cancellation closes the row"). "Delete" = set the existing marker.

**Tier C — never soft-deleted (append-only / immutable source / infra):** `calibration_log`, `rule_eval_log`,
`audit_log`, `auth_login_attempts` (append-only or already immutable); `transactions`, `weather_hourly`,
`weather_daily` (immutable ingested source data — re-ingested, not user-edited); `weather_codes` (global
reference); `auth_refresh_tokens`, `auth_revoked_tokens`, `auth_password_reset_tokens`,
`auth_mfa_recovery_codes`, `saas_admins` (security-token / admin infra with their own `consumed_at`/`expires_at`
hygiene — pruning expired credentials is security hygiene, not record deletion). Each excluded table gets a
one-line rationale in `docs/schema.md`.

**Tier D — F69 hard-delete exception (left intact):** `employee_pii` (legal PII erasure) and whole-tenant
purge. These keep a hard-delete path **only** via the audited F69 process; `employee_pii` is also in Tier A so
it soft-hides with its employee under normal operation.

## Deliverables

### A. Migration `0013_cs087_soft_delete.py` (`revision="0013"`, `down_revision="0012"`)
Generate the change **uniformly from a `TIER_A` Python list** (one code path for N tables — no per-table
hand-editing) so the pattern is identical everywhere:
- `ALTER TABLE <t> ADD COLUMN deleted_at TIMESTAMPTZ NULL, ADD COLUMN deleted_by UUID NULL;`
- `CREATE INDEX idx_<t>_live ON <t> (tenant_id) WHERE deleted_at IS NULL;` (keeps the common live-row scans
  cheap; name per the skill's `idx_<table>_<cols>` rule).
- `CREATE POLICY soft_delete_hidden ON <t> AS RESTRICTIVE FOR SELECT USING (deleted_at IS NULL OR
  current_setting('app.show_deleted', true) = 'on');` — `current_setting(…, true)` returns NULL when unset, so
  the default is "hide soft-deleted" (fail-safe toward hiding, never toward leaking a deleted row).
- Downgrade: drop the policy, the index, then the columns, for every Tier-A table, in reverse — a clean,
  reversible `0012` round-trip (the BUG-040 lesson).
- **No** change to partitioned/append-only/temporal tables; **no** FK changes.

### B. `data_access/` soft-delete helpers (reuse the existing session plumbing)
- `soft_delete(conn, table, *, tenant_id, where, actor_id)` — issues
  `UPDATE <table> SET deleted_at = now(), deleted_by = %(actor)s WHERE <where> AND deleted_at IS NULL`
  inside the caller's `open_session_context` transaction; returns the rowcount. **It does not use
  `RETURNING *`** (see edge cases — the new row is hidden from the SELECT policy). Asserts the table is in the
  Tier-A allow-list so it can't be pointed at an append-only/immutable table.
- `restore(conn, table, *, tenant_id, where)` and any admin "list including deleted" read set
  `SET LOCAL app.show_deleted = 'on'` for that transaction only (transaction-local, mirrors the existing
  `set_config(..., true)` tenant pattern), then clear implicitly at commit.
- A short module docstring states the rule: **repositories never emit `DELETE` on a domain table; they call
  `soft_delete`.**

### C. CI guard — `scripts/check_no_hard_delete.py`
- Greps `db/migrations/`, `data_access/`, `api/`, `auth/`, `compliance/`, `scripts/` (excluding `test/` and a
  single allow-listed F69 erasure module path, when it exists) for `DELETE FROM <tier-A/B domain table>` and
  `DROP TABLE <data-bearing table>`; exits non-zero on a hit (same exit-code contract as
  `check_schema_drift.py`: `0` clean, `1` violation, `2` env error). Token-table/log pruning and test teardown
  are not flagged.
- Documented in `migration/SKILL.md`'s drift section as a second CI gate.

### D. Docs
- `docs/schema.md`: add `deleted_at`/`deleted_by` to each Tier-A table entry; add a new **§1.6 Soft-delete
  lifecycle** convention (marker columns, the restrictive SELECT policy, the `app.show_deleted` admin bypass,
  the Tier-C exclusions with rationale, and the F69 hard-delete carve-out). Doc + migration move in the same
  change (the skill's rule).
- `docs/architecture/manager-ui-integration.md`: one note that list/detail reads see only live rows and that
  "delete" in the UI is a soft-delete.

## Edge cases & pitfalls (minimize bugs)
- **`RETURNING` under the restrictive SELECT policy.** `UPDATE … SET deleted_at = now() … RETURNING *`
  returns **zero rows** — the updated row now has `deleted_at` set and the `FOR SELECT` policy hides it. So
  `soft_delete` must **not** `RETURNING` the soft-deleted row; it returns `cur.rowcount`. (Tested explicitly.)
- **`UPDATE … WHERE deleted_at IS NOT NULL` (restore) is invisible by default.** Postgres applies `SELECT`
  policies to the rows an `UPDATE` reads, so a restore cannot find a soft-deleted row unless
  `app.show_deleted='on'` is set first. `restore()` sets it; a plain restore without it is a documented no-op,
  not an error. (Tested.)
- **Idempotent soft-delete.** `soft_delete` filters `AND deleted_at IS NULL`, so deleting an already-deleted
  row updates nothing (rowcount 0) and never overwrites the original `deleted_at`/`deleted_by`.
- **`current_setting('app.show_deleted', true)`** must use the **missing_ok=true** form everywhere; the unset
  default must evaluate to hide-deleted, never error (mirrors the `app.tenant_id` fail-closed pattern from
  `0001`).
- **Migrator/ETL bypass.** Migrations and ETL run as the `BYPASSRLS` migrator, so they see all rows including
  soft-deleted — correct for backfills; the restrictive policy only governs `app_rw`/`app_ro`.
- **`employee_pii` restricted grants** — it already restricts DML to `secrets_rw`; the new columns + policy
  must not widen that grant. Soft-deleting an employee soft-deletes its PII row through the `secrets_rw` path.
- **Uniqueness vs soft-deleted rows.** Tier-A PKs are natural/composite (`(tenant_id, location_id)` etc.), so a
  soft-deleted `stores` row still occupies its key — re-creating the "same" store id while the old one is
  soft-deleted would collide. **Decision:** keep PKs as-is (collision surfaces as a normal constraint error the
  app maps to a clear "a deleted record with that id exists — restore it instead" message); do **not** add
  partial unique indexes in this story (that is per-table design, deferred). Flagged, not silently ignored.
- **Children of a soft-deleted parent.** Soft-deleting a `stores` row does **not** auto-soft-delete its
  `stations`/`shifts` (no cascade on a flag). The app soft-deletes a parent and its dependents explicitly
  (documented in the helper); the restrictive read policy still hides each independently. A future trigger-based
  cascade is out of scope.
- **Solver regeneration (future).** Once the solver (F11) lands, regenerating a draft must **soft-delete**
  superseded `schedules`/`shifts`, never `DELETE`+reinsert — documented now so the unbuilt path starts correct.
- **No pytest.** Tests are stdlib `unittest`; DB tests DB-skip on `DATABASE_URL`/`GATEWAY_DATABASE_URL`,
  mutations rolled back (`raise Rollback`).

## Verification (end-to-end)
1. `alembic upgrade head` applies `0013`; `alembic downgrade 0012 && alembic upgrade head` round-trips clean.
2. New `test/test_soft_delete_lifecycle.py` (live DB): for a representative Tier-A table (e.g. `stores`): a
   seeded row is visible to `app_rw`; `soft_delete` hides it from `app_rw` SELECT; the row still physically
   exists (migrator sees it); `app.show_deleted='on'` reveals it to `app_rw`; `restore` brings it back;
   re-soft-deleting is idempotent; `soft_delete` returns rowcount without `RETURNING` leaking the row.
3. New `test/test_soft_delete_coverage.py` (static + live): **every** Tier-A table has `deleted_at`,
   `deleted_by`, the `idx_<t>_live` partial index, and a `soft_delete_hidden` restrictive SELECT policy; **no**
   Tier-C table has them. Keyed off a single shared `TIER_A`/`TIER_C` list so it can't drift from the migration.
4. `python3 scripts/check_no_hard_delete.py` exits `0` on the current tree and `1` on a planted
   `DELETE FROM stores` fixture.
5. `scripts/check_schema_drift.py` reports zero drift (schema doc updated in the same change).
6. Full existing suites stay green — `test_f61*` (59), `test_api_foundation` (9), `test_isolation_gate` (14) —
   no RLS regression; extend `test_isolation_gate` only if the restrictive policy changes a counted assertion.

## Critical files
- **New:** `db/migrations/versions/0013_cs087_soft_delete.py`; `scripts/check_no_hard_delete.py`;
  `test/test_soft_delete_lifecycle.py`, `test/test_soft_delete_coverage.py`;
  `docs/plans/CS-087-soft-delete-lifecycle.md` (this file).
- **Edited:** `data_access/` (new `soft_delete`/`restore` helpers + module docstring rule),
  `docs/schema.md` (§1.6 + per-table columns), `docs/architecture/manager-ui-integration.md`,
  `migration/SKILL.md` (reference the new CI guard), `Backlog.md` + `FEATURES.md` (CS-087 status).
- **Reuse (do not reimplement):** `data_access/session.py` (`open_session_context`/`tenant_session`, the
  transaction-local `set_config` pattern for `app.show_deleted`); `app_current_tenant()` and the existing
  `tenant_isolation` policy (untouched); the `scripts/check_schema_drift.py` exit-code contract.

## Scope guard — explicitly OUT
- FK `ON DELETE CASCADE → RESTRICT/SET NULL` swap across the schema → **deferred follow-up** (dormant under the
  no-hard-delete rule; the F69 purge relies on tenant CASCADE).
- The F69 GDPR erasure pipeline itself (PII hard-delete, tenant purge, retention schedule) → **F69/CS-069**.
- Partial unique indexes that let a natural key be reused after soft-delete → per-table design, deferred.
- Trigger-based parent→child soft-delete cascade → out of scope.
- Soft-delete on append-only/immutable/token tables → never (Tier C).

## Orchestration (per the user's instruction: Codex does dev **and** tests)
- **Codex** (`codex exec`, `gpt-5.6-terra`, high reasoning) develops the full slice **and** writes + runs the
  tests, iterating until `0013` applies/reverses clean, the two new soft-delete tests pass, the CI guard works,
  drift is zero, and all existing suites stay green. Hand Codex this plan verbatim + the `0011`/`0012` migration
  idiom to mirror and the "no pytest / stdlib unittest" rule.
- **Claude** reviews the delivered code for introduced bugs & uncovered edge cases (the `RETURNING`/restore
  policy interactions, the `show_deleted` fail-safe default, the migrator bypass, the helper allow-list),
  **files a Backlog bug story per finding**, and once tests are green **commits and pushes to GitHub**
  (trunk-based `main`; standalone `git push`; trailer `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`).
- Additive only (new columns + restrictive policies + helpers + guard); no change to existing FKs, RLS tenant
  policies, or data.
