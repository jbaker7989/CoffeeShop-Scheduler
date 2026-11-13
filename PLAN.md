# Plan — Convert Coffee Shop Scheduler to multi-tenant SaaS

## Summary

Convert the single-tenant Maven Roasters deployment into a multi-tenant SaaS that hosts many independent coffee-shop clients on shared infrastructure. **Tenancy model: shared Postgres schema with `tenant_id` on every row, isolated by Row-Level Security policies.** Customer profile: SMB coffee shops (1–10 stores per tenant). The existing Maven Roasters data becomes the seed/demo tenant.

The change touches every existing feature (F1–F59) because every table, query, span and UI route needs a tenant dimension. Eleven new features (F60–F70) cover the SaaS-specific surface: onboarding, auth/RBAC, billing, quotas, admin surface, tenant-scoped observability, isolation tests, GDPR data subject rights and tenant routing.

---

## 1. Architectural overview

### What changes

| Layer | Today | After SaaS |
|---|---|---|
| **Identity** | None | Per-tenant users; email/password; JWT with `tenant_id` claim; tenant from URL |
| **Database** | Postgres, one logical database | Same Postgres, shared schema, `tenant_id` on every domain table, RLS policies |
| **Routing** | Single Next.js app, no tenant | Subdomain (`acme.coffeescheduler.com`) or path prefix (`/t/acme/`) resolver middleware |
| **Agents (A2A)** | Hardcoded single context | Every A2A call carries `tenant_id` in the request envelope; agents verify before acting |
| **Solver (CS-016)** | Single solver process | Shared solver, tenant-scoped inputs, tenant_id propagated as CP-SAT parameter |
| **Rule engine (CS-013)** | Single jurisdiction (NY + NYC) | Per-tenant jurisdiction; rule table becomes `(tenant_id, rule_id)` composite key |
| **Observability (F36–F41)** | Single trace_id | Every span/log/metric carries `tenant_id` as a resource attribute; per-tenant Grafana variables |
| **Background jobs (F16)** | n8n cron, single tenant | n8n templates parameterised by tenant; per-tenant job isolation in the queue |
| **UI** | Manager + C-Suite | Add **SaaS admin** (separate); manager + C-Suite become tenant-scoped |
| **Billing** | None | Stripe integration; plan tiers; usage metering; webhook handling |
| **Quotas** | None | Per-tenant rate limits; storage caps; 429 responses with tenant context |
| **Secrets** | Single `.env` | Per-tenant API keys (SMS, weather, etc.) in a `tenant_secrets` table with app-level encryption |

### What does not change

- The agent architecture (Scheduler, Demand Forecast, Labor Compliance, Staff Comms).
- The deterministic engine (OR-Tools CP-SAT, F11).
- The observability stack (LGTM, F36–F41).
- The Next.js UI patterns (manager/C-Suite, F30–F59).
- The SDD + TDD process (`test/SKILL.md`).
- The data fixtures for the demo tenant (the Maven Roasters workbooks stay as the seed dataset).

---

## 2. Tenancy model in detail

### `tenants` table (new)

| Column | Type | Notes |
|---|---|---|
| `id` | `UUID PRIMARY KEY` | Internal ID |
| `slug` | `TEXT UNIQUE NOT NULL` | URL-safe, e.g. `maven-demo`, `acme-coffee` |
| `name` | `TEXT NOT NULL` | Display name |
| `status` | `ENUM('trial','active','suspended','cancelled')` | Lifecycle state |
| `plan` | `ENUM('starter','pro','enterprise')` | Plan tier |
| `jurisdiction` | `TEXT NOT NULL` | e.g. `NY-STATE+NYC`, `CA`, `WA` — drives rule engine |
| `timezone` | `TEXT NOT NULL` | IANA tz, drives scheduler horizon |
| `default_locale` | `TEXT NOT NULL DEFAULT 'en-US'` | |
| `created_at` | `TIMESTAMPTZ NOT NULL DEFAULT now()` | |
| `suspended_at` | `TIMESTAMPTZ NULL` | |
| `metadata` | `JSONB NOT NULL DEFAULT '{}'` | Extensible per-tenant config |

### `tenant_id` on every domain table

Every table that currently holds business data gets a non-nullable `tenant_id UUID NOT NULL REFERENCES tenants(id)` column. Composite indexes replace the existing single-column indexes: every existing index becomes `(tenant_id, <original columns>)`. The migration adds the column nullable, backfills from a constant, then sets `NOT NULL`.

Tables affected (all of F47's 21 tables): `employees`, `employer_config`, `labor_rules`, `school_calendar`, `holidays`, `consent_waivers`, `rule_eval_log`, `store_profiles`, `stations`, `throughput_standards`, `coverage_rules`, `fixed_tasks`, `shift_rules`, `cost_targets`, `demand_adjustments`, `calibration_log`, `transactions`, `weather_hourly`, `weather_daily`, `weather_codes`, `product_taxonomy`, `events`.

### Row-Level Security (Postgres native)

One RLS policy per table, e.g.:

```sql
ALTER TABLE labor_rules ENABLE ROW LEVEL SECURITY;
ALTER TABLE labor_rules FORCE  ROW LEVEL SECURITY;         -- enforce on the table owner too
CREATE POLICY tenant_isolation ON labor_rules
  USING      (tenant_id = current_setting('app.current_tenant_id', true)::UUID)
  WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)::UUID);
```

Three correctness points that are easy to get wrong and would make isolation fail **open**:
- **`FORCE ROW LEVEL SECURITY`** — by default Postgres does not apply RLS to the table *owner* or any `BYPASSRLS` role. The app must connect as a dedicated non-owner `app_rw` role (DML grants only, no `BYPASSRLS`); only the migration/ETL role bypasses. Without `FORCE` + a non-owner role, the policies are silently a no-op.
- **`WITH CHECK`** — blocks a client from inserting/updating a row with another tenant's `tenant_id`; `tenant_id` is always derived from context, never from the request body.
- **`current_setting(..., true)`** — the `true` returns NULL when unset, so a query with no tenant context matches no rows (fail-closed) instead of raising.

The application sets the tenant context at the start of every transaction:

```sql
SET LOCAL app.current_tenant_id = '<uuid>';
```

Connection pooling: PgBouncer in **transaction mode** with `app.current_tenant_id` set per transaction by the SQLAlchemy layer. No persistent session-level state. Note `SET LOCAL` only applies inside an explicit transaction — every request/job must `BEGIN`, set the GUC, then query; the session middleware asserts it is set before the first statement. The pooled-connection context-bleed test (§9) proves the setting never leaks into another tenant's reuse of the same connection.

### Tenant resolution

Middleware on every HTTP request:
- Extracts `tenant_id` from (in order): JWT claim, subdomain, `X-Tenant-Id` header, URL path.
- Sets the Postgres `app.current_tenant_id` for the transaction.
- Verifies the authenticated user belongs to that tenant; 403 otherwise.

For A2A calls between agents: the `tenant_id` is in the request envelope (per A2A spec). Each agent validates `envelope.tenant_id == current_tenant_id` before acting.

---

## 3. Auth, RBAC, tenant routing

### Identity

- Per-tenant user accounts (`users` table: `(tenant_id, id, email, password_hash, role, ...)`).
- Email + password (SMB-appropriate); optional magic link later.
- JWT issued on login with claims: `sub` (user id), `tenant_id`, `role`, `exp`.
- Sessions: short-lived JWT (1h) + refresh token (30d), rotated on use.
- Password hashing: argon2id.

### RBAC

Four roles per tenant:

| Role | Scope | Can do |
|---|---|---|
| `owner` | Tenant | Everything: settings, billing, invites, all stores |
| `manager` | One or more stores | Schedule, call-out approval, view analytics for assigned stores |
| `staff` | Self | View own schedule, request swaps (F14), receive SMS |
| `viewer` | Tenant | Read-only across the tenant |

Plus a fifth, cross-tenant role: `saas_admin`. Lives in a separate `saas_admins` table (no `tenant_id`); accesses the SaaS admin UI only; can impersonate (with audit log row) for support.

### Tenant routing

URL convention (default): path-based `/t/<slug>/...`. Subdomain (`<slug>.coffeescheduler.com`) supported via wildcard TLS cert + middleware. Both resolve to the same Next.js app; the slug drives the tenant context.

Tenant switcher in the UI for users in multiple tenants (common for SMB owners with 2–3 shops): a header dropdown lists tenants, switching re-issues the JWT.

---

## 4. Per-tenant configuration

Per-tenant overrides for everything currently hardcoded for Maven Roasters:

| Config | Today | After |
|---|---|---|
| Jurisdiction | Hardcoded NY | Per-tenant (`tenants.jurisdiction`) — drives rule engine lookup |
| Operating hours | Hardcoded 06:00–22:30 | Per-tenant per-store via `store_profiles` (already per-row) |
| Coverage rules | Shared sheet | Per-tenant in `coverage_rules` |
| Cost targets | Per-store | Per-tenant per-store in `cost_targets` |
| Demand adjustments | Shared | Per-tenant in `demand_adjustments` |
| Throughput standards | Shared | Per-tenant; can fall back to a default `throughput_standards_template` seeded per jurisdiction |
| Weather source | Open-Meteo only | Default Open-Meteo; per-tenant override (future: paid providers) |
| SMS provider | Twilio shared | Default shared Twilio account; per-tenant override later |
| Rule table (F10) | 31 NY rules | Per-tenant rule set; seeded from `jurisdiction_rules_template` table on tenant creation |
| School calendar | NYC only | Per-tenant; seeded per jurisdiction; per-tenant override allowed |

New `tenant_settings` table for non-tabular config (feature flags, integration toggles, branding).

---

## 5. SaaS-specific features (new)

| ID | Title | Size | Priority | Notes |
|---|---|---|---|---|
| **F60** | Tenancy primitives (schema migration, `tenants` table, RLS policies, composite indexes) | L | **P0** | Single biggest code change; affects every existing feature |
| **F61** | Tenant onboarding & provisioning (signup, slug generation, first-run wizard, default config seeding) | M | P0 | New tenant flow; lands the seed-tenant experience |
| **F62** | Auth + per-tenant RBAC (users, JWT, password hashing, four roles, login/logout/refresh) | M | P0 | Blocks every UI surface |
| **F63** | Per-tenant configuration surface (jurisdiction, hours, rules, integrations, branding) | M | P1 | Required to onboard tenant #2 |
| **F64** | SaaS admin UI (tenant list, suspend/activate, impersonate-with-audit, plan management) | M | P1 | Support tool; required for production |
| **F65** | Billing integration (Stripe — plans, subscriptions, webhooks, invoices) | M | P1 | Revenue; can ship without for trial tenants |
| **F66** | Usage tracking & per-tenant quotas (solver runs, SMS, storage, API rate limits, 429s) | M | P2 | Required before charging for usage |
| **F67** | Tenant-scoped observability (OTel `tenant_id` resource attribute, per-tenant Grafana variables, log filters, alerts) | S | P1 | Cheap; small change to F37 instrumentation |
| **F68** | Multi-tenant isolation test suite (cross-tenant query attempts must fail; CI gate) | M | **P0** | Gate before any second tenant is provisioned |
| **F69** | Data subject rights (GDPR-style export/delete per tenant) | S | P2 | Required if EU customers ever land |
| **F70** | Tenant routing (subdomain + path, tenant switcher in UI) | M | P0 | Blocks every UI surface |

Each new feature follows the existing SDD + TDD process (`test/SKILL.md`); spec items numbered as `ACn` and traced via `spec: F#.ac#`.

---

## 6. Updates to existing features (F1–F59)

Every feature that reads or writes business data picks up a `tenant_id` dimension. Grouped by behaviour:

### Data plane (F42–F47)

- **F42, F43:** add `tenant_id` to the migration template and to the Alembic env script (every revision autogenerates `tenant_id` on new tables).
- **F44 (ETL):** the seed ETL extracts a constant `tenant_id = maven_demo` and backfills it on every row; idempotent on re-run.
- **F45 (data access layer):** every repository method takes an explicit `tenant_id` argument; the SQLAlchemy session issues `SET LOCAL app.current_tenant_id` at the start of every transaction.
- **F46 (Excel export):** exports for a given tenant only; cross-tenant export is a `saas_admin`-only operation.
- **F47 (schema doc):** every table documented with `tenant_id` column, composite indexes, RLS policy. Add the RLS section to the schema doc.

### Engine (F9–F11)

- **F9 (forecast):** trained per tenant on first calibration cycle, or shared model with per-tenant features (decision deferred — see assumptions).
- **F10 (rule engine):** rules become `(tenant_id, rule_id)` composite key; engine loads only the rules for the current tenant's jurisdiction.
- **F11 (solver):** inputs filtered by tenant; solver output tagged with `tenant_id`.

### Observability (F36–F41)

- **F37 (SDK instrumentation):** every span, metric, log gains a `tenant_id` resource attribute (constant for the request lifecycle).
- **F40 (Grafana dashboards):** dashboards gain a `$tenant` template variable; the multi-tenant overview uses one panel per top-N tenant.

### UI (F30–F35)

- **F30, F31, F33, F34, F35:** every route gains `tenant_id` resolution from the URL; every query is tenant-scoped; navigation shows tenant name + switcher.

### Analytics (F52–F59)

- **F52, F56 (umbrellas):** the `store selector` becomes a `tenant selector` (single tenant for manager, all-tenant view for SaaS admin).
- **F53–F55, F57–F59:** every aggregate is grouped by `tenant_id`; the SaaS admin view shows tenant-vs-tenant comparison (optional new feature, deferred).

### Compliance & rules

- **F19 (verification pass):** becomes per-jurisdiction; the seed tenant is NY; other jurisdictions use template rule sets until verified.
- **F26 (Coverage ↔ Law reconciliation):** runs per-tenant.

### Backlog

- Every existing CS-### story gains a `tenant_id` consideration in its detailed specifications.
- New CS-### stories map to F60–F70.

---

## 7. Migration plan (single-tenant → SaaS)

Sequenced as a single cutover, not a parallel run (parallel run adds ops complexity without a clear benefit for a single synthetic dataset):

1. **Schema migration.** Add `tenants` table; add `tenant_id` column (nullable) to every domain table; **backfill in throttled batches** (chunked by PK — a single `UPDATE … WHERE tenant_id IS NULL` locks and bloats the big partitioned tables `transactions`/`rule_eval_log`/`weather_hourly`); build composite indexes with `CREATE INDEX CONCURRENTLY`; add `NOT NULL` via `ADD CONSTRAINT … NOT VALID` then `VALIDATE CONSTRAINT` (non-blocking); then `ALTER TABLE … FORCE ROW LEVEL SECURITY`; create policies (with `WITH CHECK`). Snapshot every table before starting.
2. **Backfill tenant row.** Insert the demo tenant (`slug = maven-demo`, `name = "Maven Roasters (demo)"`, `plan = 'pro'`, `jurisdiction = 'NY-STATE+NYC'`).
3. **Application code update.** Every repo, every agent, every UI route gains the tenant dimension. Deploy as a single release behind a feature flag that locks the app to one tenant until F68's isolation tests pass.
4. **Run F68 isolation tests.** With the flag off (single-tenant mode), tests attempt cross-tenant reads/writes and must fail at the DB level (RLS), not just the app layer.
5. **Enable multi-tenant mode.** Flip the flag; second synthetic tenant can be provisioned; smoke test the full path (signup → onboarding → schedule generation).
6. **Decommission single-tenant code paths.** After one week of multi-tenant mode without regression, remove the single-tenant flag and the hardcoded `maven_demo_uuid` constants.

The migration is gated on F68 — no second tenant is created until the isolation suite passes.

---

## 8. New SaaS-specific components

### SaaS admin UI (F64)

A separate Next.js route group (`/admin/...`) gated by the `saas_admin` role:
- Tenant list with filter (status, plan, jurisdiction), search by name/slug, sort by MRR/usage.
- Tenant detail: profile, plan, usage meters, suspended state, audit log.
- Actions: suspend, activate, cancel, impersonate (with mandatory reason + audit row), change plan.
- Global usage dashboard: total storage, solver-runs/hour, SMS sent/hour, error rate per tenant.
- Tenant-creation form (manual provisioning for sales-led onboarding).

### Billing (F65)

- Stripe products: `starter` ($X/mo per store), `pro` ($Y/mo per store), `enterprise` (contact).
- Stripe Customer per tenant; subscription item per store (or flat per tenant for `pro`).
- Webhook handler (`stripe.customer.subscription.updated`, `invoice.payment_failed`, etc.) updates `tenants.status` and `tenants.plan`.
- Invoice generation via Stripe; emailed by Stripe (no in-app invoicing UI).
- Grace period on `invoice.payment_failed`: 7 days to `suspended`, then data export + delete option in admin.

### Usage tracking & quotas (F66)

- Counter tables: `usage_counters(tenant_id, metric, period_start, count)` for `solver_runs`, `forecast_calls`, `sms_sent`, `storage_bytes`, `api_calls`.
- Increment on each event; periodic rollup to `usage_periods(tenant_id, metric, period, count)` for billing.
- Per-tenant rate limits: token bucket per metric; 429 response with `Retry-After` and a JSON body identifying the tenant.
- Plan-tier limits in `plan_limits(plan, metric, max)`; checked at the rate-limit middleware.

### Tenant-scoped observability (F67)

- One-line addition to every OTel span: `resource.attributes["tenant_id"] = tenant_id`.
- Grafana datasource provisioning gains a `$tenant` variable sourced from a `tenants` Postgres query.
- Loki log queries gain `| json | tenant_id="acme-coffee"` as default filter for tenant-scoped dashboards.
- Alertmanager routes include `tenant_id` label; SaaS-wide alerts (any tenant above error threshold) route to the on-call rotation.

### GDPR data subject rights (F69)

- `GET /api/v1/tenants/<id>/export.zip` — full tenant data export (Postgres dump + workbooks + OTel audit trail) signed and time-limited URL.
- `DELETE /api/v1/tenants/<id>` — soft-delete (status=`cancelled`, data retained for 30 days), then hard-delete background job (Postgres `DELETE` + object-storage bucket removal + OTel pipeline retention expiry).
- Both operations audit-logged and visible in the SaaS admin UI.

### Tenant routing (F70)

- Next.js middleware matches `/t/:slug/*` and `<slug>.coffeescheduler.com` patterns.
- Resolves slug → tenant_id, sets request context, attaches to the JWT claim on first hit.
- Tenant switcher in the manager UI: dropdown lists tenants the user belongs to; selecting re-issues the JWT and reloads.
- Wildcard TLS cert (`*.coffeescheduler.com`) provisioned via Let's Encrypt DNS-01 challenge.

---

## 9. Test cases and verification scenarios

### Unit / contract

- **Tenant resolver:** unknown slug → 404; valid slug → sets context; cross-tenant user → 403.
- **RBAC middleware:** every role × every endpoint matrix tested (owner can X, manager can't Y, etc.).
- **JWT issuance / rotation:** tamper detection, expiry, refresh-token rotation.

### Integration / isolation (F68 — the gate)

- **Cross-tenant SELECT attempts:** connect as tenant A's role, query tenant B's rows; assert 0 rows returned (RLS denies). Test every table.
- **Cross-tenant INSERT attempts:** connect as tenant A, insert a row with `tenant_id = B`; assert either the INSERT is rejected or the row is invisible to B.
- **Cross-tenant UPDATE/DELETE:** same shape.
- **Multi-tenant aggregator:** queries that intentionally span tenants (SaaS admin dashboards) work as expected; all other queries do not.
- **A2A cross-tenant:** agent A sends a request to agent B with `envelope.tenant_id = A`; B verifies against its own `current_tenant_id` and rejects if mismatched.
- **Background job isolation:** tenant A's forecast cron cannot read tenant B's sales data.

### End-to-end

- **Two-tenant smoke:** provision tenant A and tenant B; generate schedule for A; verify B cannot see A's shifts; switch user to B; verify B sees only B.
- **Onboarding happy path:** signup → slug generated → first-run wizard → tenant provisioned → demo data seeded → first schedule generated.
- **Stripe webhook:** create subscription in Stripe test mode → webhook fires → tenant status updates; cancel in Stripe → webhook → tenant suspended.
- **GDPR export:** request export → receive signed URL → download → verify completeness.
- **GDPR delete:** soft-delete → verify data still accessible to admin → 30 days later hard-delete job runs → verify data gone.

### Performance / load

- **Connection pooling under load:** 100 concurrent tenants × 10 req/s; PgBouncer transaction-mode pool holds; no session-state leaks.
- **Solver per-tenant latency:** worst-case solver run with N=10 tenants × weekly schedule fits in < 30 s.
- **OTel cardinality:** `tenant_id` as a resource attribute (not a metric label) keeps Prometheus cardinality bounded.

---

## 10. Sequencing

The critical path (each step unblocks the next):

1. **F47 + F60:** schema doc + tenancy primitives (the foundation). Block on nothing.
2. **F62:** auth + RBAC. Block on F60.
3. **F68:** isolation test suite. Block on F60, F62. **Gate before any second tenant.**
4. **F70:** tenant routing. Block on F62.
5. **F61:** onboarding flow. Block on F62, F70.
6. **F45, F44 updates:** data layer + ETL with `tenant_id`. Block on F60.
7. **F67:** tenant-scoped observability. Block on F60.
8. **F63:** per-tenant configuration surface. Block on F60, F62.
9. **F64:** SaaS admin UI. Block on F62, F70.
10. **F65:** billing. Block on F61.
11. **F66:** usage + quotas. Block on F61, F65.
12. **F69:** GDPR data subject rights. Block on F60.

The whole cutover (steps 1–5) is the smallest viable SaaS — second tenant can be provisioned once these land. Steps 6–12 harden and monetise.

---

## 11. Explicit assumptions and defaults

Recorded so the plan can move forward without further questions:

1. **Forecast model sharing** (per F9 update): default to **shared model with per-tenant features** (jurisdiction, day-of-week encodings, per-tenant intercept). Per-tenant training is expensive at SMB scale and rarely worth it; revisit if data shows otherwise. (This is a notable cost decision — could be wrong; flag for re-evaluation after 90 days of multi-tenant production data.)
2. **SMS provider:** default to a **shared Twilio account**; per-tenant Twilio is a later feature (F65.5).
3. **Domain strategy:** default to **path-based** (`/t/<slug>/...`) for the demo and first tenants; subdomain support added in F70 but not required for MVP.
4. **Region:** default to **single region** (us-east-1); data residency per-region is a Phase 4 enterprise feature.
5. **Encryption:** at-rest encryption is the Postgres default; per-tenant application-level encryption of `tenant_secrets` is the default; per-tenant KMS keys is a Phase 4 enterprise feature.
6. **Migration cutover:** default to **single cutover** behind a feature flag, not parallel-run. Parallel-run adds ops complexity without benefit for a single synthetic dataset.
7. **Backfill idempotency:** default to **`ON CONFLICT (tenant_id, natural_key) DO NOTHING`** for ETL re-runs; manual reconciliation script for any partial failures.
8. **SaaS admin impersonation:** default to **mandatory audit row** with reason field; no silent impersonation ever.
9. **Plan limits:** Starter = 1 store, Pro = up to 10 stores, Enterprise = unlimited (placeholder); exact pricing deferred to sales.
10. **Trial period:** default to **14 days**, no card required; status = `trial`; auto-converts to `cancelled` at end of trial unless a card is added.
11. **Compliance scope:** SOC 2 Type II prep deferred to Phase 4 (after first paying customers); GDPR-style export/delete lands in F69 from day one because it's cheap.
12. **Multi-currency:** out of scope for MVP; USD only.
13. **Per-tenant rule verification (F19):** rule verification is a per-jurisdiction template problem; each new tenant inherits the verified rule set for its jurisdiction. New jurisdictions require explicit verification before tenants can be created there.

---

## 12. Risks and what to watch

| Risk | Mitigation |
|---|---|
| **RLS misconfiguration** lets one tenant read another's data | F68 isolation test suite is the gate; CI fails the build if any cross-tenant read returns rows |
| **`tenant_id` missing on a new query** silently returns all rows | SQLAlchemy event listener asserts `tenant_id` is set on the session before any query; raise on miss |
| **Connection pool state leaks across tenants** | PgBouncer transaction mode; `SET LOCAL` per transaction; SQLAlchemy session middleware verifies `current_tenant_id` matches the JWT at the start of every request |
| **Cardinality explosion in metrics** if `tenant_id` is used as a metric label | Use `tenant_id` as a resource attribute (Otel convention), not as a label; per-tenant dashboards via variable substitution |
| **ETL re-run during cutover** loses or duplicates data | Idempotent upserts (`ON CONFLICT`); explicit pre-cutover snapshot of every workbook |
| **Per-tenant solver contention** (one tenant's big schedule job starves others) | Per-tenant worker pool or per-tenant job queue with fair-share scheduling; defer to a later feature if not needed at MVP scale |
| **Stripe webhook replay / out-of-order events** | Webhook idempotency keys; event log table; reconciliation cron |
| **Compliance scope creep** (a customer demands SOC 2 before they're willing to pay) | SOC 2 prep deferred but documented as Phase 4; sales owns setting expectations |

---

## 13. What this plan explicitly does not decide

- **Pricing numbers** — sales decision.
- **Brand name for the SaaS** (the repo name is currently "CoffeeShop-Scheduler"; the SaaS may rebrand) — marketing decision.
- **Whether the SaaS ships with subdomain routing on day one** — engineering decision in F70; default is path-based.
- **Multi-region / data residency** — deferred to Phase 4.
- **SOC 2 timeline** — sales / legal decision; prep is deferred.

These are recorded as open so they don't become implicit assumptions later.

---

## 14. Definition of done (for the SaaS cutover)

The SaaS is "shippable" when **all** of the following are true:

1. F47 + F60 + F62 + F68 + F70 + F61 land.
2. The Maven demo tenant is provisioned and runs end-to-end (schedule generated, viewed in UI, call-out workflow exercised).
3. A second synthetic tenant is provisioned and runs end-to-end without manual intervention.
4. F68's isolation test suite passes in CI; cross-tenant read attempts return zero rows.
5. F67 dashboards show per-tenant traffic.
6. Stripe integration handles a test subscription end-to-end (F65).
7. README + Backlog + FEATURES.md updated to reflect the multi-tenant state.
8. F69 export and delete have been exercised end-to-end against a test tenant.

After these eight, the SaaS can take paying customers. The remaining SaaS features (F63, F64, F66, F69 hardening, F67 polish) ship in subsequent releases.

---

## 15. Completeness addendum — security, lifecycle & operations

Closes the gaps a completeness review surfaced. These are **acceptance criteria for existing features (F60–F70)**, not new IDs. Items PLAN already covers are marked ✓ with a pointer; the rest are additions.

### 15.1 RLS enforcement correctness (→ F60, gated by F68)
- `FORCE ROW LEVEL SECURITY` on every table + app connects as a **non-owner `app_rw` role** without `BYPASSRLS` (see §2). Owner-bypass is the most common shared-schema RLS failure.
- `WITH CHECK` on every policy so cross-tenant **writes** are rejected, not just read-filtered; `tenant_id` always derived from context, never the request body.
- `SET LOCAL` requires an explicit transaction (§2); middleware asserts the GUC before the first statement. ✓ pooling/transaction-mode already in §2.

### 15.2 Auth hardening (→ F62)
- ✓ Refresh-token rotation + argon2id already in §3. **Add:** asymmetric JWT signing (RS256/ES256) with key rotation, validate `iss`/`aud`, reject `alg=none`; **per-request** re-check of tenant `status` + user `is_active` (so suspension takes effect within one short access-token TTL) backed by a revocation list.
- **Missing today:** password-reset flow (time-boxed single-use token), login rate-limiting / lockout / credential-stuffing defence, and optional TOTP **MFA for `owner`/`manager`** (only `saas_admin` has it).

### 15.3 Audit log (→ F62/F64)
- A dedicated **append-only** `audit_log` table (`app_rw` gets `INSERT` only — no `UPDATE`/`DELETE`) with retention + export, covering auth events, admin actions, and impersonation (records both impersonator and impersonated). ✓ impersonation audit row is in §8; this makes the store itself tamper-evident.

### 15.4 User & membership lifecycle (→ F62)
- **Invitations:** `tenant_invitations(tenant_id, email, role, token, expires_at, accepted_at)` — owner invites by email → accept creates the user. §3 lists `owner` "invites" as a capability but no flow/table exists yet.
- Consider separating **identity** (`users`: email + credential) from **membership** (`tenant_memberships`: user × tenant × role) so one person joins several tenants without duplicate credentials — matches the multi-shop owner + tenant switcher in §3.
- Deactivating a user revokes live sessions (ties to §15.2 revocation).

### 15.5 PII & data minimization in the AI / observability path (→ F67, F37)
- **Span/log scrubbing** via an OTel span processor + Loki redaction: no SSN/address/DOB, no raw SMS bodies, no LLM prompt/response payloads with employee PII. Adding `tenant_id` to spans must not leak the tenant's PII into traces.
- Agent prompt context built only from the current tenant's data; no tenant data into shared caches or model fine-tuning. Carry the existing single-tenant PII discipline forward per tenant.

### 15.6 Scale-safe migration (→ F60, F44) — ✓ folded into §7 step 1
Batched backfill, `CREATE INDEX CONCURRENTLY`, `NOT NULL` via `NOT VALID`→`VALIDATE`, pre-cutover snapshot; revisit partitioning for tenant skew (`(tenant_id, date)` or date-partition + leading-`tenant_id` index).

### 15.7 DB-level noisy-neighbor controls (→ F60, F66)
Per-tenant/role `statement_timeout`, `work_mem` caps, connection limits, and a fair-share solver-job queue, so one tenant can't exhaust the shared pool or CPU. Extends §12's "per-tenant solver contention" risk down to the database tier.

### 15.8 Background-job & ETL tenant context (→ F44, F16, F48)
Jobs touching many tenants **loop over tenants explicitly**, opening a transaction and `SET LOCAL app.current_tenant_id` per tenant, running as `app_rw` (never the `BYPASSRLS` role); per-tenant failures dead-letter rather than aborting the batch. §9 tests background isolation; this specifies the mechanism.

### 15.9 Caching (cross-cutting, gated by F68)
Any shared cache (Redis/runtime cache) keys **must** be tenant-prefixed; verified by an F68 cache-isolation test.

### 15.10 Routing & web security (→ F70, F62)
- **Reserved-slug denylist** at signup (`admin`, `www`, `api`, `app`, `auth`, `billing`, `status`, `static`, `assets`, …) — else a tenant registering `admin` collides with the admin surface.
- Slug renames redirect the old subdomain or are disallowed.
- **Custom domains** (`schedule.acme.com` via CNAME + per-domain managed TLS) are a later feature beyond F70's wildcard-cert scope — flag the size.
- **CORS** locked to tenant origins; **CSRF** protection and secure cookie flags (`HttpOnly`, `Secure`, `SameSite`) on the session path.

### 15.11 Billing completeness (→ F65, F66)
- ✓ Webhook idempotency/replay already in §12. **Add:** Stripe **webhook signature verification**; **downgrade enforcement** when current usage exceeds the new plan (block or require reduction first); proration, annual plans, **tax (Stripe Tax/VAT)**, dunning emails, free-plan abuse controls.
- ✓ `usage_counters` (§8) already bills from real metered events, not a proxy — keep it.

### 15.12 Data lifecycle & compliance (→ F69)
- ✓ 30-day soft-delete → hard-delete already in §8. **Add:** the explicit timeline incl. **backup purge** and a reactivation window; **employee-level** right-to-be-forgotten (an individual employee, not just the whole tenant); and a documented **per-tenant restore** path (shared-schema PITR is global — restoring one tenant uses export→reimport). Backups encrypted.

### 15.13 Multi-jurisdiction is a content dependency, not just code (→ F10, F19)
- ✓ Assumption #13 notes per-jurisdiction verification. Make the **blocking** nature explicit: only NY Part 146 + NYC rules and the NYC calendars exist today, so onboarding a non-NY tenant is blocked until that jurisdiction's verified rule set and school/holiday calendars are authored — a content/legal effort, not a schema change.

### 15.14 Additional isolation & security tests (→ F68, extends §9)
RLS owner-bypass test (prove `FORCE` is on), `WITH CHECK` cross-tenant insert/update rejection, pooled-connection **context-bleed** test, JWT `alg=none`/tamper/expired-refresh tests, impersonation privilege-escalation test, cache-key isolation test, plan-downgrade enforcement test, and a PII-scrubbing assertion over emitted spans/logs.
