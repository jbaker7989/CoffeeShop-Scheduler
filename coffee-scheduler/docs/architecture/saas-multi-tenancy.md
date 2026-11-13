# Multi-tenancy — SaaS conversion architecture

**Status:** Proposed · **Owner:** TBD · **Decision date:** 2026-11-12
**Decisions (from Plan Mode):** shared schema with `tenant_id` on every row · SMB coffee shops (1–10 stores per tenant) · Maven Roasters data becomes the demo / seed tenant.

---

## Summary

Convert the single-tenant Maven Roasters deployment into a multi-tenant SaaS serving SMB coffee shops. Every domain table gets a `tenant_id` foreign key; Postgres Row-Level Security policies enforce isolation. A new `tenants` table is the root; per-tenant users, RBAC, configuration, billing, and observability stack on top. The existing Maven data (3 stores, 40 staff, 31 rules) is backfilled as the seed tenant.

The LLM/agent/solver architecture is unchanged in shape — the tenant context propagates as a request-scoped variable, and the deterministic engine doesn't care which tenant it's solving for. What's new is everything *around* the engine: auth, billing, onboarding, SaaS admin, and isolation tests.

---

## 1. Architectural shift

| Layer | Today (single-tenant) | Tomorrow (multi-tenant SaaS) |
|---|---|---|
| Identity | None (no auth) | Per-tenant users + cross-tenant SaaS admins |
| Data plane | Postgres stack (F42–F47), not started | Same Postgres stack + `tenant_id` on every table + RLS |
| Tenant routing | None (single deployment) | Subdomain (`acme.coffeescheduler.com`) or path prefix (`/t/acme/`) |
| Configuration | Hardcoded NY jurisdiction, Maven's 3 stores | Per-tenant jurisdiction, stores, hours, integrations |
| Manager UI | Per-store, scoped to one tenant | Same UI, scoped to current tenant; tenant switcher for users in multiple tenants |
| C-Suite UI | Single company view | Single-tenant view; SaaS admin UI is a separate app |
| Billing | None | Stripe: customer = tenant, plan tier = Starter / Pro / Enterprise |
| Observability | OTel stack (F36–F41), per-call traces | Same stack + `tenant.id` resource attribute on every span |
| Background jobs | n8n scheduled workflows | Per-tenant workflows with isolation; one tenant can't starve another's job |

The agent platform (Scheduler, Demand Forecast, Labor Compliance, Staff Comms, Payroll/HCM) is unchanged. The solver, forecaster, and rule engine all receive `tenant_id` as input context and read tenant-scoped data. They do not implement isolation — the database does, via RLS.

---

## 2. Data model changes

### 2.1 New `tenants` table (root)

```sql
CREATE TABLE tenants (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  slug          TEXT UNIQUE NOT NULL,             -- URL-safe, e.g. "acme"
  name          TEXT NOT NULL,
  status        TEXT NOT NULL CHECK (status IN ('trial','active','suspended','churned')),
  plan          TEXT NOT NULL DEFAULT 'starter' CHECK (plan IN ('starter','pro','enterprise')),
  jurisdiction  TEXT NOT NULL DEFAULT 'NY-STATE',  -- F10 reads this
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  trial_ends_at TIMESTAMPTZ,
  settings      JSONB NOT NULL DEFAULT '{}'
);
```

### 2.2 `tenant_id` on every domain table

Every existing table from F47 gets `tenant_id UUID NOT NULL REFERENCES tenants(id)`. Tables affected: `employees`, `labor_rules`, `school_calendar`, `holidays`, `consent_waivers`, `rule_eval_log`, `store_profiles`, `stations`, `throughput_standards`, `coverage_rules`, `fixed_tasks`, `shift_rules`, `cost_targets`, `demand_adjustments`, `calibration_log`, `transactions`, `weather_hourly`, `weather_daily`, `product_taxonomy`, `events`. New tables (`schedules`, `forecasts`) also carry `tenant_id`.

### 2.3 Composite indexes

Every table's existing indexes get a `tenant_id` prefix. Example:

```sql
-- Before:
CREATE INDEX idx_labor_rules_rule_id ON labor_rules(rule_id);

-- After:
CREATE INDEX idx_labor_rules_tenant_rule_id ON labor_rules(tenant_id, rule_id);
```

Postgres query planner uses the leading column for tenant filtering; the second column accelerates within-tenant lookups. `UNIQUE` constraints that currently span rows globally (e.g. `rule_id` unique in `labor_rules`) become `(tenant_id, rule_id)` so two tenants can independently use the same `rule_id`.

### 2.4 Row-Level Security (the isolation guarantee)

```sql
ALTER TABLE employees ENABLE ROW LEVEL SECURITY;
ALTER TABLE employees FORCE ROW LEVEL SECURITY;          -- critical: see note below

CREATE POLICY tenant_isolation ON employees
  USING      (tenant_id = current_setting('app.tenant_id', true)::UUID)   -- read / update-visibility
  WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::UUID);  -- block cross-tenant writes
```

The `true` second arg to `current_setting` returns NULL if the setting is missing — the policy then matches no rows, which is the desired default (deny). The explicit `WITH CHECK` stops a client from *inserting or updating* a row with another tenant's `tenant_id`; the application must always derive `tenant_id` from the request context (JWT), never from the request body.

**`FORCE ROW LEVEL SECURITY` is mandatory and easy to miss:** by default Postgres does **not** apply RLS policies to the table *owner* (or any `BYPASSRLS` role). If the application connects as the role that owns the tables — the common default — every policy above is silently a no-op and isolation fails open. Two defences, used together: (1) `FORCE ROW LEVEL SECURITY` on every domain table, and (2) the application connects as a dedicated, non-owner `app_rw` role that has only DML grants and **not** `BYPASSRLS`. Only the migration/ETL role carries `BYPASSRLS`, and application code never connects as it.

Every domain table gets this policy. The application sets `app.tenant_id` at the start of each transaction from the JWT claim; the database then enforces isolation regardless of what the application code does — provided the two defences above hold.

### 2.5 Connection pooling

PgBouncer in **transaction mode**. Per-session `SET LOCAL app.tenant_id = ...` at the start of each transaction. PgBouncer's transaction mode is required because session-mode would hold the tenant setting across the connection's lifetime, which is incompatible with pooling.

**Gotcha:** `SET LOCAL` is scoped to the *current transaction*, so every request must open an explicit transaction, set the GUC, then query. If any code path runs in autocommit (a bare `SELECT` outside `BEGIN`), the setting never takes effect and the query runs with no tenant context — which, with `FORCE` RLS, denies all rows (a visible failure, not a silent leak, which is the safe direction). The ORM session middleware must therefore wrap every request in a transaction and assert `app.tenant_id` is set before the first statement. The pooled-connection **context-bleed test** (§11) proves tenant A's `SET LOCAL` never survives into tenant B's reuse of the same physical connection.

---

## 3. Auth + RBAC

### 3.1 Per-tenant users

```sql
CREATE TABLE tenant_users (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  email         CITEXT NOT NULL,
  password_hash TEXT NOT NULL,                          -- argon2id
  role          TEXT NOT NULL CHECK (role IN ('owner','manager','staff','read_only')),
  is_active     BOOLEAN NOT NULL DEFAULT TRUE,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_login_at TIMESTAMPTZ,
  UNIQUE (tenant_id, email)
);
```

Email uniqueness is **per tenant**. The same email can register in two tenants — common for SMB owners who run multiple shops.

### 3.2 Cross-tenant SaaS admins

```sql
CREATE TABLE saas_admins (
  id            UUID PRIMARY KEY,
  email         CITEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  mfa_secret    TEXT,                                   -- TOTP
  is_active     BOOLEAN NOT NULL DEFAULT TRUE
);
```

SaaS admins exist outside the tenant hierarchy. They authenticate to a separate endpoint, can list / suspend / impersonate any tenant, and every action is audit-logged.

### 3.3 Login flow

```
POST /auth/login
{ "tenant_slug": "acme", "email": "jane@acme.coffee", "password": "..." }
→ 200 OK { "token": "<jwt>", "expires_at": "...", "role": "owner" }
```

JWT carries `tenant_id`, `user_id`, `role`, `is_saas_admin`. Middleware extracts these and sets Postgres session variables before each request.

### 3.4 Impersonation

SaaS admin can act as any tenant user for support purposes:

```
POST /admin/impersonate
{ "tenant_id": "...", "user_id": "..." }
→ 200 OK { "impersonation_token": "<jwt>", "expires_in": 3600 }
```

The impersonation JWT carries `impersonated_by: <saas_admin_id>`. Every action taken under impersonation is logged with both the impersonator and the impersonated user.

---

## 4. Tenant onboarding

### 4.1 Signup

```
POST /tenants
{ "name": "Acme Coffee", "slug": "acme", "email": "owner@acme.coffee", "password": "...", "jurisdiction": "NY-STATE" }
```

Validates slug uniqueness and slug format (`^[a-z0-9][a-z0-9-]{1,30}[a-z0-9]$`). Creates tenant + first `tenant_users` row (role = owner) in a single transaction.

### 4.2 Email verification

Owner gets a verification email; until verified, the tenant is in `pending_verification` status (an extension of the `tenants.status` enum) and can sign in but cannot invoke the solver.

### 4.3 Trial period

`trial_ends_at = now() + 14 days`. A nightly job suspends tenants whose trial has expired without conversion. Suspension blocks all API calls except `/billing/*`.

### 4.4 First-run wizard

After first login, the owner is walked through:

1. **Add first store** — single form (name, address, time zone, coordinates for weather). Or upload a CSV.
2. **Add first employee** — single form (name, email, role). Or upload a CSV.
3. **Set operating hours** — per store, per day-of-week.
4. **Pick plan tier** — Starter (free, 1 store, 10 employees) / Pro ($99/mo per location, up to 10 stores) / Enterprise (custom).

Each step is skippable; the wizard resumes on next login until complete.

### 4.5 Provisioning

Tenant creation triggers a provisioning transaction that:

- Inserts the tenant row.
- Seeds default `labor_rules` filtered to the tenant's jurisdiction (NY-STATE / CA / etc.) — populated from the canonical rule templates per F10.
- Seeds default `labor_standards` (Store_Profile template, Coverage_Rules defaults).
- Sends the welcome email.

Provisioning is idempotent — re-running on an already-provisioned tenant is a no-op.

---

## 5. Per-tenant configuration

Most configuration lives in dedicated tables (so it gets the same RLS treatment). A small amount lives in `tenants.settings` JSONB for fast-changing per-tenant flags.

| Setting | Where | Why |
|---|---|---|
| Jurisdiction | `tenants.jurisdiction` | F10 reads to filter applicable rules |
| Plan tier | `tenants.plan` | F65 enforces quotas |
| Operating hours | `store_profiles` (per store) | F11 reads; CS-005 encoding pattern |
| Coverage rules | `coverage_rules` (per tenant) | F11 reads |
| Cost targets | `cost_targets` (per tenant) | F11 reads |
| Demand adjustments | `demand_adjustments` (per tenant) | F5 calibrates per tenant |
| SMS provider config | **non-secret** flags in `tenants.settings.sms` (shared pool vs dedicated); **credentials** in `tenant_secrets` (see below) | Per-tenant billing for SMS |
| Weather source | `tenants.settings.weather` — Open-Meteo by default, configurable | Regional overrides |
| Branding | `tenants.settings.branding` (logo URL, primary color) | Manager UI theming |
| Integration secrets | `tenant_secrets(tenant_id, key, ciphertext, ...)` — **never** plaintext JSONB | Twilio/Stripe/weather keys must be encrypted at the application layer |

> **Secrets never live in `tenants.settings` JSONB.** A settings blob is readable in backups, logs, the SaaS admin UI and any `SELECT *`. Per-tenant credentials (Twilio, Stripe, paid weather, future integrations) go in a dedicated `tenant_secrets` table with **application-level encryption** (envelope encryption via a KMS data key), decrypted only in the request/job path that needs them. The JSONB column holds non-secret config only.

---

## 6. SaaS admin surface

A new UI at `admin.coffeescheduler.com` (or `/admin` on the main domain, behind a separate auth flow):

- **Tenant list** — search by name/slug/email, filter by plan/status, paginated.
- **Tenant detail** — usage (solver runs this month, SMS sent, storage), last login, plan, payment status.
- **Suspend / activate / churn** — sets `tenants.status`; suspended tenants get 403 from the API.
- **Impersonate** — generates an impersonation JWT (see §3.4); audit-logged.
- **Plan management** — change plan, apply credit, issue refund (calls Stripe API).
- **Usage dashboard** — aggregate metrics across all tenants (MRR, active tenants, total solver runs, total SMS).
- **Audit log** — every admin action (suspend, impersonate, plan change) recorded with admin user, target tenant, timestamp, IP.

This UI is **separate** from the manager / C-Suite UI: separate React app, separate auth flow, separate deploy. SaaS admins don't accidentally land in a tenant's view.

---

## 7. Billing

### 7.1 Plans

| Plan | Stores | Employees | Solver runs / mo | SMS / mo | Price |
|---|---|---|---|---|---|
| Starter | 1 | 10 | 50 | 100 | Free |
| Pro | up to 10 | unlimited | 5,000 | 5,000 | $99 / mo / location |
| Enterprise | unlimited | unlimited | unlimited | unlimited | Custom |

### 7.2 Stripe integration

- `Customer` per tenant.
- `Subscription` per tenant, one active at a time.
- Webhooks handled by an n8n workflow: `customer.subscription.updated`, `invoice.paid`, `invoice.payment_failed`.
- Payment failure → grace period (7 days) → suspend tenant.

### 7.3 Usage tracking

A nightly job aggregates per-tenant usage from `rule_eval_log` (solver runs proxy), OTel metrics (SMS sent), and Postgres size queries. Stored in a `tenant_usage` table; quotas enforced at API gateway level (return 429 with quota context when exceeded).

---

## 8. Observability

Every OTel span emitted by an agent gets a `tenant.id` and `tenant.slug` resource attribute (per F37). Concretely:

```python
Resource.create({
  "service.name": "labor-compliance-agent",
  "service.version": "0.1.0",
  "tenant.id": str(tenant.id),
  "tenant.slug": tenant.slug,
  "deployment.environment": settings.env,
})
```

Grafana dashboards (F40) gain a `tenant` template variable so panels filter by tenant. Loki log queries scope by `tenant.id`. Alerts can target a single tenant (e.g. "tenant X's solver has been infeasible for 3 days") without per-tenant dashboards.

---

## 9. Migration of existing data

### 9.1 Backfill script

One-shot migration (`scripts/backfill_tenant_id.py`):

1. Insert Maven demo tenant:
   ```sql
   INSERT INTO tenants (id, slug, name, status, plan, jurisdiction)
   VALUES ('00000000-0000-0000-0000-000000000001', 'maven-demo', 'Maven Roasters (demo)', 'active', 'pro', 'NY-STATE');
   ```

2. Update every existing row in every domain table:
   ```sql
   UPDATE employees SET tenant_id = '00000000-0000-0000-0000-000000000001' WHERE tenant_id IS NULL;
   UPDATE labor_rules SET tenant_id = '...' WHERE tenant_id IS NULL;
   -- ... repeated for every domain table
   ```

3. Set `tenant_id` to `NOT NULL` on each table after backfill.

4. Idempotency: the WHERE clause ensures re-running is safe.

### 9.2 ETL update

F44's `etl_excel_to_postgres.py` adds `tenant_id` to its COPY payloads. The script reads the `tenants` table to resolve the demo tenant; future ETL runs for real tenants take `tenant_id` as a CLI arg.

### 9.3 RLS migration

Enabling RLS on a populated table is non-trivial: existing queries that don't set `app.tenant_id` will return zero rows. Migration order:

1. Backfill `tenant_id` (above).
2. Deploy application code that sets `app.tenant_id` on every connection.
3. Verify all queries return correct results.
4. Enable RLS on each table.
5. Add the policy.
6. Smoke-test cross-tenant isolation (see §11).

---

## 10. New feature breakdown (F60–F70)

| ID | Feature | Size | Priority |
|---|---|---|---|
| **F60** | Tenancy model & RLS — `tenants` table, `tenant_id` on every domain table, RLS policies, connection pooling | M | P0 |
| **F61** | Auth + per-tenant RBAC — `tenant_users`, JWT issuance, login flow, middleware, SaaS admin role | M | P0 |
| **F62** | Tenant onboarding — signup, email verification, trial, first-run wizard, provisioning | M | P0 |
| **F63** | Per-tenant configuration — jurisdiction, hours, rules, integrations, JSONB settings | M | P1 |
| **F64** | SaaS admin surface — tenant list, detail, suspend, impersonate, audit log, usage dashboard | L | P1 |
| **F65** | Billing & plan tiers — Stripe integration, plan enforcement, webhook handling, payment-failure flow | L | P1 |
| **F66** | Usage tracking & quotas — per-tenant metering, quota enforcement, 429 responses | M | P1 |
| **F67** | Tenant-scoped observability — `tenant.id` resource attribute, Grafana filters, per-tenant alerting | M | P1 |
| **F68** | Multi-tenant isolation tests — automated cross-tenant attack scenarios, RLS policy tests | M | P0 (gate) |
| **F69** | Data export / delete (GDPR) — per-tenant data export, hard delete, audit trail | M | P2 |
| **F70** | Tenant routing — subdomain or path-prefix resolution, tenant resolver middleware, TLS per subdomain | S | P0 |

F60, F61, F62, F68, F70 are P0 — they're the foundation that nothing else builds without. F63–F67 land P1 for credible demo. F69 is P2 (compliance, not blocking).

---

## 11. Test scenarios

Multi-tenant isolation is the headline safety property. The CI suite (per `test/SKILL.md`) covers:

**Isolation (must fail if violated):**
- User from tenant A receives 404 (not 403, to avoid existence disclosure) when accessing tenant B's employee / store / schedule.
- SQL injection attempt with `tenant_id = '<tenant_b_uuid>'` returns zero rows under RLS.
- API request without a JWT returns 401.
- API request with a JWT for a suspended tenant returns 403.
- Impersonation token works for the impersonated tenant but every action is logged with `impersonated_by`.

**Onboarding:**
- New signup creates tenant + owner in a single transaction; either both succeed or both fail.
- Duplicate slug is rejected with a 409.
- Email verification blocks solver invocations until verified.

**Billing:**
- Stripe `customer.subscription.updated` webhook flips `tenants.plan`.
- Payment failure puts tenant in grace period, then suspended.
- Plan tier limits enforced: Starter cannot create an 11th employee.

**Migration (one-time):**
- Backfill assigns `tenant_id = maven-demo` to every existing row.
- Post-backfill row counts match pre-backfill.
- ETL re-run with same inputs produces same data (idempotency).

**Performance:**
- Tenant-scoped query with `(tenant_id, ...)` index completes in < 50 ms p95.
- No query plan regression after enabling RLS (verify with `EXPLAIN ANALYZE`).

---

## 12. Assumptions and defaults

| Decision | Default | Risk if wrong |
|---|---|---|
| Auth method | Email + password (SMB-appropriate; no SSO in MVP) | Enterprise customers will need SAML; defer to Phase 2 |
| Region | Single US-East deployment | EU customers need data residency; defer |
| Plan tiers | Starter / Pro / Enterprise as listed | Pricing research needed before launch |
| Trial length | 14 days | Standard SaaS, may need adjustment based on conversion data |
| Stripe | Primary billing provider | Alternatives exist (Chargebee, Recurly) but Stripe is the SMB default |
| URL strategy | Subdomain (`acme.coffeescheduler.com`) for production; path-prefix for dev | Subdomain requires wildcard TLS and DNS; path-prefix is cheaper to set up |
| Impersonation scope | Full read/write for 1 hour, audit-logged | Support use case justifies the risk; capped duration limits blast radius |
| RLS bypass | Only ETL and migration roles (`BYPASSRLS`) | Application code must never connect as these roles |
| Cross-tenant analytics | Aggregate only, in SaaS admin dashboard | Per-tenant competitive intel is not exposed across tenants |
| Compliance scope | GDPR data export + delete (F69) in P2; SOC 2 prep deferred | Enterprise sales require SOC 2; plan for post-launch |

---

## 13. Sequencing recommendation

The work splits into three waves, each gated:

**Wave 1 — Foundation (P0):** F60 → F61 → F68 → F70 → F62. Lock in the data model, auth, isolation tests, routing, and onboarding. No SaaS admin UI, no billing — yet. End state: a tenant can sign up, log in, and use the existing single-tenant app with isolation guarantees.

**Wave 2 — SaaS admin + billing (P1):** F64 → F65 → F66 → F67 → F63. Add the admin surface, Stripe, usage tracking, per-tenant observability, and per-tenant configuration. End state: a paid SaaS product.

**Wave 3 — Compliance (P2):** F69. GDPR data export / delete. End state: ready for EU customers.

Each wave ends with `migration/SKILL.md` schema-drift check green and the isolation test suite green before the next wave begins.

---

## 14. Completeness addendum — security, lifecycle & operations

The sections above cover the data model, auth, onboarding, billing, observability, migration and sequencing. This addendum closes the gaps a completeness review surfaced. Each item names the feature (§10) it folds into — **no new feature IDs are minted**; these are acceptance criteria for existing ones.

### 14.1 RLS enforcement correctness (→ F60, gated by F68)
- `FORCE ROW LEVEL SECURITY` on every domain table, and the app connects as a **non-owner `app_rw` role** without `BYPASSRLS` (see §2.4). Owner-bypass is the single most common way shared-schema RLS fails open.
- Every policy has a `WITH CHECK` so a cross-tenant **write** is rejected, not just filtered on read. `tenant_id` is always derived from the JWT/job context and stripped from request bodies.
- `SET LOCAL` only works inside an explicit transaction (§2.5); the ORM wraps every request/job in one and asserts the GUC is set before the first statement.

### 14.2 Auth hardening (→ F61)
- **JWT:** asymmetric signing (RS256/ES256) with key rotation; validate `iss`/`aud`/`exp`; reject `alg=none`. Short-lived access token (≈15 min) + rotating refresh token with reuse detection.
- **Revocation:** suspension/deactivation must take effect within one access-token TTL — middleware re-checks tenant `status` and user `is_active` **per request** (not just at login), backed by a revocation list for immediate logout.
- **Password reset** (time-boxed, single-use signed token) — currently missing entirely.
- **Login protection:** rate-limiting, account lockout / backoff, and credential-stuffing defence on `/auth/login`.
- **MFA:** optional TOTP for `owner`/`manager` roles (today only `saas_admins` have it); payroll-adjacent data warrants it.

### 14.3 Audit log (→ F61/F64)
- A dedicated **append-only** `audit_log(id, tenant_id NULL, actor_id, actor_type, action, target_type, target_id, before, after, ip, user_agent, at)` table. The `app_rw` role has `INSERT` only — no `UPDATE`/`DELETE` — so entries are tamper-evident; retention and export are defined for compliance.
- Covers auth events, every admin action, and impersonation (records **both** `impersonated_by` and the impersonated user).

### 14.4 User & membership lifecycle (→ F61)
- **Invitations:** `tenant_invitations(tenant_id, email, role, token, expires_at, accepted_at)`; owner invites by email → accept creates the `tenant_users` row. Today users only exist via signup — there is no way to add a manager/staff member.
- Consider separating **identity** (`users`: email + credential) from **membership** (`tenant_memberships`: user × tenant × role) so one person can belong to several tenants without duplicate passwords — matches the "SMB owner with 2–3 shops" assumption and the tenant switcher.
- Deactivating a user revokes their live sessions (ties to §14.2 revocation).

### 14.5 PII & data minimization in the AI / observability path (→ F67, F37, Labor-Compliance & Staff-Comms agents)
- **Span/log scrubbing:** an OTel span processor + Loki pipeline redact PII before export — no SSN/address/DOB, no raw SMS bodies, no LLM prompt/response payloads containing employee PII. Adding `tenant.id` to spans (§8) must not come with leaking the tenant's PII into traces.
- **Prompt isolation:** agent prompt context is built only from the current tenant's data; no tenant data crosses into shared caches or any model fine-tuning.
- Carry the existing single-tenant PII discipline (SSN/address split to `private/`, DOB-not-age) forward **per tenant**.

### 14.6 Scale-safe migration (→ F60, F44)
- The naïve single-statement backfill in §9.1 will lock and bloat large/partitioned tables (`transactions`, `rule_eval_log`, `weather_hourly` per F47). Use: **batched** backfill (chunked by PK, throttled), `CREATE INDEX CONCURRENTLY`, and add `NOT NULL` via `ADD CONSTRAINT … NOT VALID` → `VALIDATE CONSTRAINT` (non-blocking). Snapshot every table before cutover.
- Revisit the partition key for tenant skew — `(tenant_id, date)` composite, or keep `date` partitioning with a leading-`tenant_id` index — so hot tenants don't dominate a partition.

### 14.7 DB-level noisy-neighbor controls (→ F60, F66)
- Per-tenant/role `statement_timeout`, `work_mem` caps, connection limits, and a fair-share solver-job queue, so one tenant's heavy query or schedule run can't exhaust the shared pool or CPU. §1's "one tenant can't starve another" must hold at the **database** tier, not just in n8n.

### 14.8 Background-job & ETL tenant context (→ F48, F44, nightly jobs)
- Jobs that touch many tenants (weather ingest, calibration, trial-suspension) **loop over tenants explicitly**, opening a transaction and `SET LOCAL app.tenant_id` per tenant; they run as `app_rw`, never the `BYPASSRLS` role (only the migration/ETL role bypasses). A per-tenant failure dead-letters rather than aborting the batch.

### 14.9 Caching (cross-cutting, gated by F68)
- Any shared cache (Redis, HTTP/runtime cache) keys **must** be tenant-prefixed; no cross-tenant cache reuse. Verified by an F68 cache-isolation test.

### 14.10 Routing & web security (→ F70, F61)
- **Reserved-slug denylist** enforced at signup (`admin`, `www`, `api`, `app`, `auth`, `billing`, `status`, `static`, `assets`, …) — otherwise a tenant could register `admin` and collide with `admin.coffeescheduler.com`.
- Slug renames either keep the old subdomain redirecting or are disallowed (no orphaned links).
- **Custom domains** for enterprise (`schedule.acme.com` via CNAME + per-domain managed TLS) are a later feature, not part of F70's "S" scope — flag the size.
- Wildcard-cert auto-renewal is monitored; **CORS** locked to tenant origins; **CSRF** protection and secure cookie flags (`HttpOnly`, `Secure`, `SameSite`) for the session path.

### 14.11 Billing completeness (→ F65, F66)
- Stripe **webhook signature verification** + idempotency keys + an event log (webhooks retry and arrive out of order).
- **Downgrade enforcement:** when a tenant drops to a plan its current usage exceeds (Pro→Starter with 5 stores), block the downgrade or require reduction first.
- Proration, annual plans, **tax (Stripe Tax / VAT)**, dunning emails, and free-plan abuse controls.
- Bill from real metered events, not a `rule_eval_log` proxy.

### 14.12 Data lifecycle & compliance (→ F69)
- **Churn timeline:** soft-delete (`churned`) → retention grace → hard delete → **backup purge**, with a reactivation window; today only the state exists, not the schedule.
- **Employee-level** right-to-be-forgotten: an individual employee of a tenant (not just the whole tenant) can be erased — the single-tenant PII model makes this concrete.
- **Per-tenant restore caveat:** shared-schema PITR is global; restoring one tenant without affecting others is not free. Document the limitation and provide a per-tenant export → reimport restore path. Backups encrypted.

### 14.13 Multi-jurisdiction is a content dependency, not just code (→ F10, F19)
- Provisioning seeds rules "per jurisdiction" (§4.5), but only **NY Part 146 + NYC** rules and the NYC school/holiday calendars exist today. Onboarding a non-NY tenant is **blocked** until that jurisdiction's verified rule set and calendars are authored (F19 per-jurisdiction verification). This gates the multi-jurisdiction promise and is a content/legal effort, not a schema change.

### 14.14 Additional isolation & security tests (→ F68)
Add to §11: RLS owner-bypass test (prove `FORCE` is on), `WITH CHECK` cross-tenant **insert/update** rejection, pooled-connection **context-bleed** test, JWT `alg=none`/tamper/expired-refresh tests, impersonation privilege-escalation test, cache-key isolation test, plan-downgrade enforcement test, and a PII-scrubbing assertion over emitted spans/logs.
