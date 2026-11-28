# CS-041 and CS-022 — Detailed development plan

**Stories:** CS-041 (F41, CI observability gate) and CS-022 (F26, Coverage↔Law reconciliation CI check)  
**Priority / size:** CS-041 P2 / S; CS-022 P1 / XS  
**Plan status:** Ready for execution after dependency gate below  
**Code and test author:** Codex  
**Independent completeness review and test execution:** Claude  
**Commit / push gate:** after Claude's review and test execution both pass  
**Related prerequisite:** CS-021 compliance verification pass (P1, M)

## 1. Executive summary

These stories add independent safeguards to CI:

- **CS-041** verifies observability at external-call boundaries. It must detect whether the exact HTTP, A2A, database, or tool operation under test emitted the expected span. A single span elsewhere in a test run must not make an uninstrumented call appear covered. Tests for unrelated code are not required to emit telemetry.
- **CS-022** verifies that every `Coverage_Rules` row claiming a `LAW` basis cites an extant legal rule, that the cited rule is verified for the dates the coverage row claims, and that effective-date gaps/conflicts fail CI with actionable diagnostics.

CS-022 is explicitly dependent on **CS-021**. The backlog says CS-021 is not started and identifies `NYC-FPC-COVERAGE` as still `VERIFY`. Enabling a blocking CI gate now may fail on that known prerequisite. CS-022's parser and tests can be authored against synthetic verified/unverified fixtures, but the gate must not be turned on as a required pass until CS-021 has resolved and revalidated every currently referenced law rule. Do not lower the bar or silently exempt the current failing rule to make CI green.

CS-041 can proceed independently. Keep each story in its own implementation commit/PR even if one plan document tracks both, so the CS-022 prerequisite does not block the completed CS-041 work.

## 2. Source-of-truth checks before implementation

On the current `main` branch, Codex should first re-read:

- The CS-041 and CS-022 entries in `Backlog.md` and the F41/F26 sections in `FEATURES.md`.
- `otel/telemetry.py` and existing OTel instrumentation/test patterns.
- The active CI workflow and its test/environment setup; confirm whether Postgres and canonical seed rows are available to the CI job that will run CS-022.
- `compliance.engine.reconcile_coverage`, `LAW_ID`, `rules_admin._conflicts`, the `coverage_rules` / `labor_rules` schema, migration `0002`, and current tenant seed values.
- `test/SKILL.md`, `.github`/CI conventions, and the repository's standard-library `unittest` convention. Do not add pytest solely for these stories.

Do not rely on the local developer database as a hidden CI input. The gate must run from a clean checkout with documented, deterministic fixture setup.

## 3. CS-041 — CI observability gate

### 3.1 Goal and boundary

Fail CI when an external operation is added or changed without an observable span at the correct operation boundary. The monitored categories are HTTP client/server, A2A, database, and tool invocation. The gate protects trace coverage; it must not change application behavior, require a live Collector, or turn telemetry export failure into business-operation failure.

**Important limitation to address in the spec:** an in-memory exporter only sees code paths the test actually executes. It cannot detect an uncalled, uninstrumented wrapper by itself. Pair exporter-backed boundary tests with a deterministic changed-call-site policy (manifest/static boundary inventory or enforced shared adapters), and test the policy using a deliberately uninstrumented fixture.

### 3.2 Proposed acceptance criteria

Add uniquely numbered `F41.acN` criteria to `FEATURES.md` before writing tests; map each test to the actual IDs chosen there.

| AC | Required behavior | Proof |
|---|---|---|
| **F41.ac1 — isolated exporter** | A test can install a recording/in-memory span exporter without configuring OTLP or making network calls, and can shut it down/reset it without contaminating another test. | Install/emit/read/reset test; assert the exporter sees the emitted span and a second test starts empty. |
| **F41.ac2 — boundary evidence** | Each registered external boundary (HTTP, A2A, DB, tool) has a dedicated test invoking that exact path and asserting the operation-specific span name, kind, and expected trace/parent relationship. | One explicit test per registered category and boundary; each assertion checks that specific span, not a global non-empty span list. |
| **F41.ac3 — errors are visible** | A failing external operation records an error status and exception/event evidence on its span while preserving the existing exception/result behavior. | Inject a controlled failure at each applicable boundary and assert span status plus unchanged caller behavior. |
| **F41.ac4 — changed call-site gate** | A new or modified direct external-call site must either use a registered instrumented adapter and have a matching exporter-backed test, or carry a narrowly reviewed exemption with a reason and owner. An unregistered raw call fails CI. | Gate self-test feeds an uninstrumented call fixture and expects failure; the instrumented adapter fixture passes. |
| **F41.ac5 — no live service dependency** | Focused observability tests use fakes/stubs or a disposable local test harness and succeed without Collector, remote HTTP service, external A2A peer, or production DB credentials. | Run the focused suite with endpoint variables unset and outbound network disabled. |
| **F41.ac6 — safe attributes** | The tested spans contain operation, service, and non-sensitive status attributes but no credentials, request bodies, staff PII, or unbounded/high-cardinality payloads. | Assertions inspect emitted attributes for required fields and forbid known sensitive keys/values. |
| **F41.ac7 — CI job behavior** | The CI job runs the dedicated contract suite and changed-call-site check; an intentionally uninstrumented new wrapper makes the gate fail and the corrected wrapper passes. | Gate integration test plus green workflow run on a clean checkout. |

### 3.3 Design choices to settle before implementation

1. **Exporter ownership:** use a per-test `TracerProvider` and `SimpleSpanProcessor` with `InMemorySpanExporter` (or the supported equivalent). Do not reinitialize `otel.telemetry`'s process-global provider in a test process. If production code currently fetches a global tracer directly, introduce the smallest injectable tracer seam or test fixture needed; do not add a second production telemetry bootstrap.
2. **Known boundary inventory:** record the existing monitored call sites and their expected span identifiers in a small machine-readable manifest or an equivalent code-owned registry. Include owner module, category, test identifier, and allowed exemptions. Keep dynamic data such as URL, SQL, args, tool payload, tenant/user IDs, and exception message out of span names/attributes.
3. **New call-site policy:** prefer one of two demonstrably enforceable approaches: (a) direct external API calls are prohibited outside registered adapters, and the changed-file checker rejects new call sites outside those adapters; or (b) an AST-based registry maps changed call sites to boundary tests. Validate detection against imports/aliases, multiline calls, helper wrappers, and method aliases before relying on it. If the checker cannot reliably detect a supported call shape, document the safe escape hatch and add a test; do not claim comprehensive coverage from a best-effort regex.
4. **Per-test assertions:** each boundary test clears/owns its exporter and asserts the exact expected span. Avoid a shared exporter where one unrelated span can mask a missing span in the operation under test.
5. **Context propagation:** test parent/child trace IDs for nested HTTP/A2A paths; test missing, malformed, and remote trace context is handled according to W3C propagation defaults. Do not require trace IDs to be equal across unrelated calls.
6. **Async / threaded code:** ensure spans close in `finally`/context managers on cancellation, exception, timeout, and early return. Add an async or thread propagation test only for a boundary that actually uses it; context from one concurrent request must not bleed into another.
7. **CI scope:** do not force every unit test in the project to emit a span. The requirement applies to registered external-boundary tests and new/changed call sites. This prevents unrelated pure-function tests from becoming flaky or coupled to telemetry.

### 3.4 CS-041 test matrix

- HTTP client success and failure: one fake transport call per case; assert client span and error status without network traffic.
- HTTP server/request boundary, if present: test one request through the actual ASGI middleware/dependency; assert server span and trace-context extraction.
- A2A request/response: assert client/server spans share trace ID and server span is a child; malformed/missing context gets a safe new/root context.
- Database operation: use a fake or isolated DB as appropriate; assert a DB span wraps the external call, SQL parameter values are not attached, and exception behavior is unchanged.
- Tool dispatch: assert the actual registered tool invocation creates a span; tool input/output and secrets are not attached.
- Error path: operation exception marks span ERROR and is re-raised/mapped exactly as before.
- Gate self-test: an intentionally uninstrumented wrapper is rejected; a registered wrapper with a matching boundary test passes.
- Test isolation: provider/exporter reset after success and exception; no global provider collision; no spans leak between tests or concurrent requests.

### 3.5 CS-041 edge cases and risks

- Span is created but never ended because an exception/cancellation bypasses cleanup.
- Parent context is dropped, malformed, or accidentally reused from another request.
- Exporter returns only ended spans, causing a false pass/failure due to flush order.
- A single global span makes a coverage assertion pass although the external operation under test is uninstrumented.
- New imports alias a client (e.g. `from package import Client as C`) or hide it behind a helper, bypassing a regex-only checker.
- Mocking replaces the instrumented adapter rather than the network transport, so the test proves no span.
- Telemetry configuration accidentally selects an OTLP exporter and attempts network access in CI.
- Duplicate spans from nested auto-instrumentation plus a manual span; define expected nesting/ownership and avoid double instrumentation.
- Span attributes include URL query strings, SQL, email, token, request body, tool input, or PII.
- Cardinality explosion from span names containing IDs or arbitrary tool names.
- Context propagation across async tasks/threads differs from synchronous tests.
- Test parallelism shares a global exporter/provider and creates nondeterministic assertions.

### 3.6 CS-041 implementation sequence

1. Inventory the real external-call boundaries and current instrumentation on `main`; define the enforceable policy before changing code.
2. Add the F41 acceptance criteria and the boundary manifest/adapter inventory.
3. Write failing exporter-backed boundary tests plus checker self-tests; demonstrate the intentionally uninstrumented fixture fails.
4. Add the smallest trace seams/wrappers required, preserving operation semantics and exceptions.
5. Add the changed-call-site check and CI job; verify the clean main baseline passes without broad skip/ignore rules.
6. Run focused observability tests, adjacent HTTP/A2A/database/tool tests, full suite, lint, spec traceability, test-first, spec lint, and review checklist.
7. Codex verifies its implementation; Claude performs independent completeness/security review and executes the focused and full required test commands. Only commit/push once Claude's review is clear and all required tests pass.

## 4. CS-022 — Coverage↔Law reconciliation CI check

### 4.1 Goal and dependency gate

Make it impossible for a coverage row labeled as law to silently reference a missing, unverified, or date-inapplicable labor rule. The CI check must inspect canonical rows or deterministic fixtures loaded from canonical seed data; it must not validate only a hand-maintained copy that can drift.

**Hard dependency:** CS-021 must complete first, or at minimum all current `LAW` coverage citations must be verified against primary sources before the gate is required. Backlog currently marks CS-021 not started and lists `NYC-FPC-COVERAGE` among `VERIFY` rules. Therefore the implementation of CS-022 may proceed in a preparatory branch against synthetic fixtures, but CI activation/merge is blocked until CS-021 makes the current canonical data pass. Do not weaken the rule or add a permanent allowlist for known unverified citations.

### 4.2 Proposed acceptance criteria

Add uniquely numbered F26 criteria to `FEATURES.md` before implementing tests. CS-022 test tags must reference those real IDs.

| AC | Required behavior | Proof |
|---|---|---|
| **F26.ac1 — LAW detection** | A coverage row is classified as LAW when a whole-word, case-insensitive `LAW` marker appears in the same source fields used by the application reconciliation contract; non-LAW business assumptions are not forced to cite a statute. | Fixtures cover `LAW`, `LAW (reference)`, mixed case, `LEGAL` as a non-match, and unrelated words containing `law`. |
| **F26.ac2 — citation resolution** | Every LAW coverage row contains at least one valid exact labor-rule identifier; every distinct cited identifier resolves within the same tenant; missing, malformed, duplicate-ambiguous, and cross-tenant references fail with row and ID diagnostics. | Pure tests plus a tenant-isolation integration case if the CI database is available. |
| **F26.ac3 — verified status** | Every legal-rule version that supports the claimed coverage period has `verification_status='VERIFIED'` and a valid `verified_on` date; `VERIFY`, `ASSUMPTION`, NULL, unknown status, or absent source evidence fails. | Parameterized status/evidence fixtures; unknown values fail closed. |
| **F26.ac4 — effective-date coverage** | For each LAW row, the cited verified legal-rule versions cover the coverage row's entire effective range with no gap. Use PostgreSQL daterange `[)` semantics, including unbounded lower/upper bounds. | Adjacent valid ranges pass; one-day gap and partial-coverage cases fail; unbounded cases are explicit. |
| **F26.ac5 — conflicts** | Any cited rule whose relevant interval overlaps a contradictory/unverified version, or whose binding enforcement contradicts the LAW claim, is reported as a conflict. | Overlapping/conflicting version fixtures return stable typed conflict records. |
| **F26.ac6 — deterministic CI source** | The gate loads the canonical seed/fixture or a disposable database created from migrations and seed scripts; every row is tenant-scoped and soft-delete-aware. Missing DB/seed/config is an explicit CI failure for the required gate, not an empty-row pass. | Clean-checkout CI test proves setup, row count/source identity, and no vacuous pass. |
| **F26.ac7 — actionable output** | A failure lists coverage tenant/rule, cited labor rule, affected date interval, and failed condition, without exposing employee/customer PII. | Snapshot tests pin diagnostics and prove values are deterministic. |
| **F26.ac8 — no silent exemption** | Every exemption requires a code-owned rationale and expiry/owner, is narrow to a specific test fixture or row, and cannot suppress a new LAW row. Production canonical data has no exemption for an unverified legal reference. | Gate self-test introduces an unverified LAW row and proves it fails despite unrelated documented non-LAW assumptions. |

### 4.3 Rule and interval semantics

1. **Parse the same source fields as runtime.** Start with `description`, `value`, and `basis`, matching `reconcile_coverage`; document if the CI source additionally includes a typed citation column. Do not parse arbitrary rule IDs from unrelated columns.
2. **Use exact identifier matching.** Reuse or tighten the known rule-ID grammar, then verify exact IDs against `(tenant_id, rule_id)`; do not use substring matches. Deduplicate identical references; if more than one distinct citation is intentional, validate all of them rather than selecting the first.
3. **Do not assume one effective row per rule.** `labor_rules` permits effective-dated versions under a composite key and disallows overlapping versions for the same tenant/rule. Collect every version and evaluate the union of intervals that jointly cover the coverage interval.
4. **Date range model:** use lower-inclusive/upper-exclusive PostgreSQL dateranges. Existing `coverage_rules.validity` is populated as unbounded by migration `0002`; treat SQL NULL and an unbounded range distinctly and reject NULL/empty validity for a LAW row unless there is a documented canonical conversion. A finite legal range does not fully support an unbounded coverage claim.
5. **Verification evidence:** `VERIFIED` alone is the status gate, but ensure source citation/URL and `verified_on` meet the CS-021 contract. Do not fetch URLs during CI; network checks are flaky and create SSRF risk. Validate stored evidence fields and their shape only.
6. **Enforcement consistency:** preserve `reconcile_coverage`'s hard-vs-soft conflict check; decide with F26 owners whether every LAW reference must be blocking (`HARD`/`PROCESS`) or whether `PAY_ONLY` is valid for any cited rule. Encode the approved domain semantics in tests, not ad hoc string heuristics.
7. **Scope/deletion:** query only rows visible to the canonical tenant set, exclude `deleted_at IS NOT NULL` where the table has soft-delete, and avoid treating absent rows due to RLS as a clean pass. Run using the intended CI role/context or a reviewed fixture-loading role and assert expected tenant/rule counts.

### 4.4 CS-022 test matrix

- Happy path: verified legal version fully covers a LAW coverage range; correct enforcement and source evidence; pass.
- Business assumption path: no LAW marker; no citation required; pass.
- Missing/invalid citation: LAW but no citation, malformed ID, unknown ID, duplicate exact ID, multiple distinct IDs; deterministic fail/pass semantics.
- Verification states: VERIFIED passes; VERIFY, ASSUMPTION, NULL, unknown status and missing `verified_on` fail.
- Interval boundaries: legal and coverage intervals exactly adjacent at a date pass/fail according to `[)` rules; one-day gap fails; partial overlap fails; full coverage by adjacent verified versions passes; unbounded lower/upper combinations tested.
- Tenant boundary: identical rule ID in another tenant does not satisfy a citation; RLS-hidden/missing current-tenant row fails as missing.
- Conflicting versions/enforcement: no version in force, a gap between versions, a conflicting enforcement, and mixed citations report every conflict with stable ordering.
- Source/setup integrity: fixture row count and source revision asserted; an empty database or missing connection cannot pass vacuously.
- Test mutation: add an unverified LAW row to the canonical fixture; CI gate must fail; remove/verify it; gate passes.

### 4.5 CS-022 implementation sequence

1. Confirm CS-021 status and the exact current verified rule rows; request primary-source verification through that story, not through a CI exception.
2. Add F26 acceptance criteria and document the canonical data source, date-range semantics, and whether coverage enforcement may be PAY_ONLY.
3. Implement a pure reconciliation function over typed coverage and labor-rule records; return structured findings and format diagnostics at the CLI/test boundary. Keep database loading separate.
4. Write failing unit tests first for citation/status/date/enforcement cases, then DB-backed tests for exact tenant/RLS/soft-delete query behavior.
5. Add a CI command that fails closed on absent seed data or an unreachable required database; ensure it runs after deterministic seed setup.
6. Run the gate against current canonical seed data. If it fails because a source rule remains unverified, stop and keep CS-022 blocked; do not mark the suite green by skipping the rule.
7. After CS-021 passes, activate the required CI gate, run migration/seed/regression checks, and update `FEATURES.md`/`Backlog.md` status in the same PR.

## 5. Shared SDD/TDD and ownership workflow

For each story independently:

1. Codex updates story specs first (Description, numbered ACs, edge/adversarial cases, cross-references, expected outcome) and creates a failing-first test commit.
2. Codex implements the smallest change needed to satisfy the spec and tests; no unrelated telemetry, rule-engine, CI, or schema cleanup.
3. Claude reviews the diff against every AC and edge case, checks that the gate cannot pass vacuously, and raises concrete bug stories for reproducible gaps.
4. Claude runs the focused tests, adjacent regression tests, full `unittest` discovery, Ruff, spec traceability, test-first, spec lint, review checklist, and any DB-backed migration/schema checks required by the changed code. Report skipped DB tests separately; a skipped required test is not green evidence.
5. If Claude finds a defect or test failure, Codex fixes it and Claude reruns the failed tests plus affected regressions.
6. Commit/push only after both the independent review and all required tests pass. Do not merge CS-022 into a failing main gate before CS-021 is complete.

## 6. Suggested orchestration order and release gates

1. **CS-041 first or in parallel with CS-021:** it has no dependency on CS-022 and should be deliverable independently. Keep the CI gate dedicated to external-boundary tests and the changed-call-site checker.
2. **CS-021 prerequisite:** close the outstanding primary-source verification and ensure all currently LAW-referenced rules are verified and effective-dated correctly.
3. **CS-022 after CS-021:** develop against fixtures while CS-021 is in progress if desired, but do not enable a required CI job or mark CS-022 done until canonical data passes.
4. **Final verification:** run CI from clean checkout; test an intentionally uninstrumented external wrapper and an unverified LAW reference; both must fail their respective gates. Then run the corrected versions and confirm both pass.

## 7. Non-goals and stop conditions

- Do not make every test emit at least one span; assert spans only in dedicated boundary tests.
- Do not claim static analysis proves all dynamic call paths. Unknown call forms must fail closed or use an explicit reviewed adapter.
- Do not send traces to an external Collector from unit tests or CI contract tests.
- Do not allow missing CS-022 data/DB connectivity to look like zero conflicts.
- Do not fetch legal source URLs from CI.
- Do not loosen RLS, bypass soft-delete, or use owner credentials to hide tenant-scoping defects.
- Stop CS-022 activation if CS-021 leaves a cited rule in `VERIFY`/`ASSUMPTION`, if a canonical citation is ambiguous, or if date semantics cannot be established.
- Stop CS-041 merge if the checker cannot demonstrate that a deliberately uninstrumented new boundary fails.

## 8. Definition of done

- [ ] CS-041 ACs/tests exist and every registered external boundary has exact exporter-backed evidence.
- [ ] The changed-call-site gate catches the deliberate uninstrumented fixture without broad allowlists.
- [ ] Spans preserve context, report errors, and contain no sensitive payloads.
- [ ] CS-022 ACs/tests cover citation, tenant, verification, enforcement, and date-range semantics.
- [ ] CS-021 prerequisite is satisfied before CS-022's blocking CI check is activated.
- [ ] Empty/misconfigured data cannot make either gate pass vacuously.
- [ ] Claude's independent completeness review and required test execution are green.
- [ ] Backlog/feature status and CI workflow are synchronized with delivered behavior.
- [ ] Each story is committed and pushed only after its own gates pass; CS-022 remains blocked if its dependency is unresolved.
