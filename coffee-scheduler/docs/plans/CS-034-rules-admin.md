# F34 — Rules Admin — Detailed Implementation Plan

**Owner:** CS-034 · **Status: PLANNED — not yet built** (corrected 2026-11-13; see §0)
**Depends on:** CS-013 (rule engine) · CS-021 (verification gate) · CS-022 (coverage↔law CI check) · F26 (reconciliation) · F10 (waiver expiry) · F28 (rule-change rollout / audit) · **new migration `0002`** (coverage effective-dating, §4.0)
**Estimated dev effort:** ~4d (M)

## 0. Status correction & grounding

A prior revision of this plan, `FEATURES.md` F34, and `Backlog.md` CS-034 marked this feature **"Done (2026-11-12), AC1–AC8 implemented and verified."** That was inaccurate: neither `coffee-scheduler/compliance/rules_admin.py` nor `coffee-scheduler/test/test_rules_admin.py` exists, and no validation was run. The status has been corrected to **planned**. Do not treat F34 as shipped.

This plan has also been reconciled against the **live schema** (verified 2026-11-13). The relevant facts that drive the corrections below:

- `labor_rules` **does** have `source_url`, `source_citation`, `verification_status`, `verified_on`, `notes`, and a `validity daterange` column, plus `effective_from` (NOT NULL) / `effective_to` (nullable).
- `verification_status` takes **three** values in the data: `ASSUMPTION`, `VERIFY`, `VERIFIED` (not two).
- `enforcement` takes **four** values: `HARD`, `PAY_ONLY`, `SOFT_COST`, `PROCESS` (the earlier plan omitted `PAY_ONLY`).
- `coverage_rules` columns are **only** `tenant_id, rule_id, location_id, description, value, unit, basis` — **no effective dates today** (§4.0 adds them), and `coverage_rules.rule_id` holds the coverage rule's **own** id (`COV-MIN-CREW`, `COV-KEYHOLDER-OPEN`, …), never a cited labor rule. The cited labor rule, when present, is in the free-text `description` and is extracted by the engine's `LAW_ID` regex.
- `rule_eval_log.schedule_id / shift_id / employee_id` are all **nullable**, so an admin-audit row with those null is schema-valid.
- The existing reconciliation function is `compliance.engine.reconcile_coverage(rules, coverage_rows) -> None`; it **raises** `CoverageConflict` and returns no list. §4 below adapts to that contract rather than inventing an incompatible one.

## 1. Overview

Rules Admin gives managers a read-write surface over `labor_rules`, guarded by the F26 reconciliation check and the F10/CS-021 verification gate. Every edit writes an F28 audit row to `rule_eval_log`. The UI never silently accepts a change that creates an assumption-vs-law conflict. This plan covers the backend service and API, the reconciliation logic, authorization, the edge cases, and the test suite. The Next.js frontend is a consumer of the backend API.

## 2. Acceptance Criteria (AC1–AC8)

| AC | Description | Verifying test |
|---|---|---|
| **AC1** | A manager views all `labor_rules` rows with `verification_status` badges (**all three**: `VERIFIED` / `VERIFY` / `ASSUMPTION`), `enforcement` (incl. `PAY_ONLY`), and F26 reconciliation flags. | GET `/rules` returns `rule_id, verification_status, enforcement, reconciliation_status` for every tenant row. |
| **AC2** | An **unverified** rule (`verification_status != 'VERIFIED'`) being set to a blocking `enforcement` (`HARD`/`PROCESS`) is blocked at submit (see §6 for the single, precedence-ordered message). | PATCH setting `enforcement='HARD'` on a non-`VERIFIED` rule → 400 with the §6 message. |
| **AC3** | A coverage-rule **effective-date** conflict with the underlying legal rule is surfaced inline before save, using the `coverage_rules.validity` added in migration `0002` vs `labor_rules.validity`. | PATCH producing a coverage/law date overlap → 400 listing the conflicting coverage rows. |
| **AC4** | The `consent_waivers` list shows expiry warnings with **half-open, tenant-local** semantics (§5): WV-0001 (EMP-1033) is valid **through 2027-06-25** and already expired **on 2027-06-26**. | GET `/rules/:id/waivers` flags WV-0001 within 30 days and reports `expired=true` on/after `valid_to`. |
| **AC5** | Every edit writes an F28 audit row to `rule_eval_log` with `agent='rules-admin-agent'` and before/after JSON. | After an edit, a new `rule_eval_log` row exists with that agent and `notes` containing `before`/`after`. |
| **AC6** | Setting a rule to `LAW`/blocking enforcement without a verified `source_url`/`source_citation` **or** a documented design-decision note is blocked (§6). | PATCH `enforcement='HARD'`, empty `source_url` and empty `notes` → 400 (§6 message). |
| **AC7** | The reconciliation check runs on **every** submit and presents the conflicting rows; no silent accept of an assumption-vs-law conflict. | PATCH producing any §4 conflict → 400 listing it. |
| **AC8** | Exported CSV matches the Postgres source **value-for-value** (not just row count). | Export CSV → row count **and every field** equal `SELECT * FROM labor_rules` for the tenant (§7 test compares values). |

## 3. API surface

All endpoints under `/api/rules`; the app connects as **`app_rw`** and runs `SET LOCAL ROLE app_rw` + `SET LOCAL app.tenant_id = '<uuid>'` **inside each request transaction** (pooling-safe per F43; `SET LOCAL`, not session `SET`). Missing tenant context → 401 "tenant context missing" (never a silent no-tenant that RLS would turn into zero rows).

### 3.0 Authorization (who is a "manager")
`app_rw` is a **database** role shared by the app; it is not an authorization model. Rules Admin endpoints additionally require an authenticated principal with the **`rules_manager`** capability (demo auth boundary from F30; real RBAC in F60–F70). A viewer-only principal may call the GET endpoints but receives 403 on PATCH/export-with-PII. This gate is enforced in the API layer **before** the DB transaction opens.

- **GET /rules** — paginated list; filters `verification_status` (VERIFIED|VERIFY|ASSUMPTION), `enforcement` (HARD|PAY_ONLY|SOFT_COST|PROCESS), `rule_category`. Each row carries `reconciliation_status ∈ {clean, conflict, unverified_law}` and `etag` (see §3.3).
- **GET /rules/:rule_id** — one rule, all `labor_rules` columns, plus `reconciliation_warnings[]`, `waiver_warnings[]`, `source_url_valid`, and `etag`.
- **PATCH /rules/:rule_id** — edit a subset of columns; pre-submit checks in §3.3; optimistic concurrency in §3.4.
- **GET /rules/:rule_id/waivers** — `consent_waivers` for the rule's employees with §5 expiry semantics.
- **POST /rules/export** — CSV via a single `COPY (SELECT … )` (§7 / AC8).

### 3.3 PATCH pre-submit checks (all must pass before any mutation)
1. **Authorization** (§3.0): principal has `rules_manager`, else 403.
2. **Optimistic concurrency** (§3.4): `If-Match` etag matches current row, else 409.
3. **Verification + LAW-source gate** (§6): one ordered check, one message.
4. **F26 reconciliation** (§4): run on the *proposed* row; any conflict → 400 listing rows.
5. **Waiver expiry** (§5): warning only, never blocks.

### 3.4 Optimistic concurrency (lost-update prevention)
Every rule row exposes an `etag` = hash of (`rule_id`, `xmin`) or of a monotonic `updated_at` the service maintains. PATCH **requires** `If-Match: <etag>`; if it no longer matches (another manager edited the row first), the service returns **409 Conflict** with the current row, and the UI re-renders the fresh values for the manager to re-apply. Without this, concurrent disjoint-field edits silently clobber — RLS and the append-only log do **not** prevent lost updates.

### 3.5 Audit row (AC5)
INSERT into `rule_eval_log` as `app_rw` (schema allows the nulls): `eval_id`=uuid4, `schedule_id`=null, `shift_id`=null, `employee_id`=null, `rule_id`=edited rule, `result`='PASS', `premium_cost_usd`=0, `evaluated_at`=now, `agent`='rules-admin-agent', `notes`=JSON `{action:'rule_edit', before:{…}, after:{…}, manager_id:'…'}`. This table is append-only (UPDATE/DELETE revoked), so the audit trail is immutable.

## 4. Reconciliation logic (F26)

### 4.0 Schema change — coverage effective-dating (migration `0002`)
AC3 requires coverage/law date overlap, but `coverage_rules` has no dates. Migration `0002` (CS-047 follow-on) adds `coverage_rules.validity daterange` (plus optional `effective_from`/`effective_to` generated from it) with a GiST exclusion to forbid overlapping windows per `(tenant_id, rule_id)`, mirroring `labor_rules`. AC3 and §4.1 depend on this migration; until it lands, AC3 is **not** implementable and must not be claimed as passing.

### 4.1 Conflict types
| Type | Definition (grounded in real columns) | Result |
|---|---|---|
| `LAW_citation_missing` | A `coverage_rules` row whose `description` contains `LAW` cites **no** labor rule_id (via the `LAW_ID` regex) | 400 + rows |
| `LAW_citation_unverified` | The cited labor rule exists but `verification_status != 'VERIFIED'` | 400 + rows |
| `assumption_vs_law` | A `LAW` coverage row cites a labor rule whose `enforcement` is **non-blocking** (`SOFT_COST`, `PAY_ONLY`, or `PROCESS` — i.e. not in `{HARD, PROCESS}` per the engine's `BLOCKING_ENFORCEMENT`; note `PROCESS` **is** blocking) | 400 + rows |
| `effective_date_overlap` | After `0002`: `coverage_rules.validity` overlaps `labor_rules.validity` for the cited rule in a way the proposed edit would break | 400 + rows |

### 4.2 Implementation — reuse the engine's contract
The service calls the **existing** `compliance.engine.reconcile_coverage(rules, coverage_rows)` (which extracts the cited rule from `description` via `LAW_ID` and **raises** `CoverageConflict`), wrapping it to collect rather than raise:

```python
def reconcile_for_admin(proposed_rule, all_rules, coverage_rows):
    # Apply the proposed edit to an in-memory copy, then reuse the engine's citation-based check.
    trial = [proposed_rule if r.rule_id == proposed_rule.rule_id else r for r in all_rules]
    conflicts = []
    try:
        reconcile_coverage(trial, coverage_rows)          # citation + enforcement checks (engine)
    except CoverageConflict as exc:
        conflicts.append({"conflict_type": "law_citation", "details": str(exc)})
    conflicts += effective_date_conflicts(proposed_rule, coverage_rows)  # §4.0, after migration 0002
    return conflicts
```

The earlier draft's `SELECT … FROM coverage_rules WHERE rule_id = %s` was wrong: `coverage_rules.rule_id` is the coverage rule's own id, never the cited labor rule — that query matches nothing. Citation matching is by `description` regex only.

## 5. Waiver expiry logic (CS-013b) — half-open, tenant-local

Waivers are **half-open**, matching the engine: a waiver is valid for `valid_from <= day < valid_to`, so it is **expired on `valid_to` itself** (CS-013 test: WV-0001 PASS through 2027-06-25, FAIL on 2027-06-26).

- `today` is computed in the **tenant timezone** (`tenants.timezone`), never the server's local date (avoids the AS-OF/D6 off-by-one).
- `days_until_expiry = valid_to - today` (tenant-local). `expired = today >= valid_to`.
- `expired` → banner "Expired on `<valid_to>`; rule re-evaluates without the waiver." (Not "expires today".)
- `0 < days_until_expiry <= 7` → "Action required: expires in N day(s)".
- `7 < days_until_expiry <= 30` → "Expires within 30 days".
- Display only; never blocks a submit.

## 6. Verification + LAW-source gate (AC2 + AC6) — one ordered check

AC2 and AC6 overlap (both fire when an unverified rule is set to blocking enforcement). Resolve with a **single** check and **one** precedence-ordered message, so the response is deterministic:

1. If the edit sets `verification_status='VERIFY'` and provides **no** resolving `source_url`/`source_citation`, **and** does not set blocking enforcement → 400 **"rule not yet verified; verify in CS-021 first."**
2. If the edit sets a **blocking** `enforcement` (`HARD`/`PROCESS`) while the rule is not `VERIFIED` **and** there is neither a verified source citation **nor** a design-decision `notes` rationale → 400 **"design decision required for unverified LAW rule."** (This case wins when both would apply.)
3. `source_url`, when provided, is validated per §6.1; a verified citation or a non-empty design-decision note satisfies the gate.

CS-021 is **Not started**; until it ships, step 1's "verify in CS-021 first" path has no gate to hand off to — the service must still **block** (fail-closed) rather than accept, and say so.

### 6.1 Source-URL fetch — SSRF-guarded
Validating a manager-supplied `source_url` means the **server fetches an attacker-influenceable URL** — a classic SSRF sink. Required mitigations:
- Scheme allow-list `https` only; reject `http`, `file`, `gopher`, `ftp`, redirects to non-https.
- Resolve the host and **reject** private/loopback/link-local/metadata ranges (`127/8`, `10/8`, `172.16/12`, `192.168/16`, `169.254/16`, `::1`, `fc00::/7`), re-checked **after** DNS resolution (defeat rebinding).
- Hard timeout (≤3 s), no following cross-host redirects, cap response size, HEAD/GET only.
- Non-200 (or any guard failure) does **not** block the save: store `source_url_valid=false` and an audit note "source URL unverified; manager attested manually." The fetch is advisory, never a hard dependency in the write path.

## 7. Edge / adversarial cases (minimizing bugs)

| Case | Mitigation |
|---|---|
| Coverage date range overlaps the legal rule (post-`0002`) | §4.1 `effective_date_overlap` → 400 listing rows; UI highlights the ranges. |
| Cross-tenant `rule_id` access | RLS + `SET LOCAL app.tenant_id` → other tenants' rows invisible; a request naming another tenant's rule returns **404** (not 403 — no existence disclosure, per F68). |
| Waiver `valid_to` is today | `expired=true` (half-open); banner "Expired on `<date>`"; rule re-evaluates without the waiver; audit row records the base result. |
| Export CSV must equal Postgres | `COPY (SELECT * FROM labor_rules) TO STDOUT WITH (FORMAT csv, HEADER true)` as `app_rw` (RLS already scopes to tenant — the explicit `WHERE` is redundant). No Python transform ⇒ no drift; §7 test compares **values**, not just the count. |
| `LAW` rule, no citation | §6 step 2 → 400; row not mutated; UI inline error on `enforcement`. |
| `source_url` returns 404 / is internal | §6.1: save proceeds, `source_url_valid=false`, audit note; internal/private URLs rejected outright (SSRF). |
| **Concurrent edits (lost update)** | §3.4 `If-Match` etag → 409 on stale edit; the loser re-fetches and re-applies. RLS/append-only log alone do **not** prevent this. |
| Empty-body PATCH (no fields) | 200 `{matched:false, modified:false}`, no audit row. |
| PATCH with identical values (no-op) | Treated as not-modified: `before == after`, no audit row (AC5's before≠after invariant preserved). |
| `verification_status='ASSUMPTION'` | Allowed without a source URL; audit row recorded. |
| `enforcement='PAY_ONLY'` set | Permitted; non-blocking for `BLOCKING_ENFORCEMENT`; shown with its own badge; `assumption_vs_law` treats it as non-blocking. |
| Field with embedded comma/newline/quote (`notes`, `consequence`) | CSV via `COPY … FORMAT csv` quotes correctly; §7 value-compare catches any escaping drift. |

## 8. Test suite (stdlib — **no pytest in this repo**)

Per `coffee-scheduler/test/SKILL.md`, tests are **stdlib `unittest`/assertion scripts** run with `python3`, tagged `# spec: F34.acN`; DB-touching tests skip unless `DATABASE_URL` is set and use **`psycopg`** (never `psql`). The backend is exercised via its Python service functions (and, where an HTTP layer exists, via `unittest` + the framework's test client — the framework is **named in §10**, not assumed). Each of AC1–AC8 has at least one test; CI greps `spec: F34.acN` and fails if any AC lacks a test or any test cites a non-existent AC. Reconciliation and export tests run against the live DB (31 rules, 8 coverage rows, 1 tenant). AC8's test asserts **value equality** per column, including `validity`, `numeric`, nulls, and quoted text — not just row count.

## 9. Non-goals
View + edit only (no create-from-scratch — F28/F29); no solver trigger (CS-016 publishes via F28 reload); multi-tenant RBAC beyond RLS is F60–F70; xlsx write-back is retired (all writes to Postgres); visual polish is a follow-up.

## 10. Files to create / modify
**Create:** `coffee-scheduler/db/migrations/versions/0002_coverage_effective_dating.py` (§4.0); `coffee-scheduler/compliance/rules_admin.py` (service — pure functions + an HTTP adapter for the **named** framework, e.g. FastAPI/Flask; pick one and state it here before building); `coffee-scheduler/test/test_rules_admin.py` (stdlib, ≥1 test/AC).
**Modify:** `coffee-scheduler/FEATURES.md` (F34 → corrected status + AC vocabulary); `coffee-scheduler/Backlog.md` (CS-034 → corrected status); `coffee-scheduler/compliance/__init__.py` (export if needed).

## 11. Success metrics
AC1–AC8 pass in the stdlib suite; `reconcile_for_admin` < 50 ms on the live DB; waiver computation < 10 ms; CSV export of 31 rows < 200 ms; ruff clean; no un-issued `TODO`s; **migration `0002` has a tested upgrade *and* downgrade** (per F43 AC3).

---
*Plan reconciled against the live schema and corrected 2026-11-13. F34/CS-034 is **planned, not built**.*
