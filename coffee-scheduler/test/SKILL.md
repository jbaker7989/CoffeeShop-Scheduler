# Skill: test

**Specification-Driven Development is our process. Test-Driven Development is how we implement it.** Every story ships with a written spec, failing tests derived from that spec, and a passing test suite at the end.

- **SDD** owns the *order of artifacts*: spec → tests → implementation → review. The spec is the contract.
- **TDD** owns the *test-first practice* within that order: write the test, watch it fail, make it pass.

Both gate the merge. A PR with a great implementation but no spec, or with tests that don't trace to spec items, does not merge.

## When this skill applies

Every entry in `FEATURES.md` (F1–F35), every bug fix, and every refactor. No change is "done" until the spec, the tests, the lint, the review checklist, and the CI machine checks below all pass.

## Story spec format (required for SDD)

A story in `FEATURES.md` must have these blocks before any code is written. If it doesn't, the first step of the workflow is to add them.

- **Description** — what and why, in prose.
- **Acceptance criteria** — numbered, testable (`AC1`, `AC2`, …). Each is one assertion the system must satisfy. No "etc." No "and so on."
- **Edge / adversarial cases** — the boundaries and bad inputs the implementation must handle (waiver expiry, missing data, unverified rules, etc.).
- **Cross-references** — the data tables, rule IDs, agent cards, and other stories this depends on.
- **Expected outcome** — a one-line summary for the backlog table.

A criterion is testable if you can write `assert` (or its equivalent) in one line. If you can't, rewrite the criterion.

## Workflow

1. **Read the story.** Open the matching entry in `FEATURES.md`. Confirm it has the spec blocks above. If not, finish the spec first — that's part of the story, not a separate task.
2. **Refine the acceptance criteria.** Walk each one with the implementer and reviewer; tighten until every criterion is a single testable assertion. Add edge / adversarial cases the author missed. Update `FEATURES.md` in a spec-only commit.
3. **Derive tests from spec items.** For each `ACn`, plan at least one test. Map every test to a spec item with a `spec: F#.ac#` tag — see "Spec traceability" below.
4. **Write the tests first.** Three is a floor, not a ceiling — use more when the story warrants it. The required three classes are:
   - **Happy path** — the canonical example from `AC1` (or whichever AC is the canonical case).
   - **Edge case** — one boundary condition the spec names (waiver expiry, forecast quantile, solver infeasibility on a minor past curfew, etc.).
   - **Adversarial case** — one bad input the system must reject, log, or fail loud on.
5. **Run the tests. They must fail** for the documented reason. If a test passes before any production code exists, the test is wrong — rewrite it. Commit the failing tests on their own.
6. **Write the minimum production code to turn them green.** Per the Ponytail principle: no extra features, no abstraction nobody asked for. Use the helpers and patterns the codebase already has.
7. **Run the full suite + linter.** Every test passes. No regressions in adjacent tests. Linter is clean.
8. **Review against the human checklist below.** Fix anything it flags before opening the PR.
9. **Update `FEATURES.md` status and merge.** Status moves to `Done` (or the next status in its lifecycle) in the same PR that lands the impl. A PR that lands code without updating the spec status is incomplete.

## Spec traceability

Every test file references the story and acceptance criterion it covers. The tag is a comment near the top of each test (or test class), in the form:

```python
# spec: F31.ac2 — Weekly schedule view shows shift assignments
def test_weekly_schedule_view_shows_assignments():
    ...
```

```ts
// spec: F34.ac3 — Rules admin blocks an unverified rule marked LAW
test("rules admin blocks unverified LAW rule", () => { ... });
```

CI greps every test file for `spec: F\d+\.ac\d+`. The build fails if:

- any test has no spec reference, **or**
- any `ACn` in `FEATURES.md` has no test referencing it.

The traceability is one-way (test → spec); the spec is the source of truth.

## Tests live where?

- Project-level tests: `coffee-scheduler/test/` (this directory) for shared logic, or a `tests/` subfolder per agent under `agents/<name>/tests/` — match the convention the agent's neighbours use.
- File naming: `test_<unit>_<behaviour>.py` or `test_<feature>.py`. One test file per story is fine.
- Fixtures and synthetic data (the `coffee_shop_*.xlsx` workbooks, the weather join) are fixtures, not tests.
- The `spec: F#.ac#` tag goes at the top of the test function or test class, not the file, so a single file covering multiple ACs is readable.

## Defect patterns to test for (data-generation & validation code)

These are the recurring failure modes that have actually bitten this repo's deterministic
generator/validator scripts (`data/build_*.py`, `data/calibrate_*.py`, `data/validate_*.py`). Every one
of them **passed all validators at the time** — a green suite proved the one happy data snapshot, not the
code. When step 4 asks for an "edge" and an "adversarial" test, these are the edges to reach for; a test
that just re-asserts the happy case with different numbers is **not** an edge test. The root lesson across
all of them: **fail loud and closed; never let a default, a hard-coded count, or a shared assumption
absorb a bad input silently.**

| # | Pattern — *"smell"* | Rule | The test that catches it | Taught by |
| --- | --- | --- | --- | --- |
| D1 | **Hard-coded count coupled to the data** — a constant (quota, row count, partition size, expected-length) that must equal a property of the input is frozen in the source. | Derive it from the input, or assert it at runtime with a descriptive failure. Never freeze a number the data owns. | Feed an input that changes that property; assert the script adapts **or** exits with a typed, descriptive error — never a bare `KeyError`/`IndexError`. | BUG-001 |
| D2 | **Silent-default lookup** — `dict.get(k, 0)` / `.get(k, "")` / `fillna(0)` on a key that *must* be present. | For a lookup that must be total, assert coverage and raise listing the offenders. A default is only legal when the absent case is genuinely expected and documented. | Inject an unmapped/renamed/whitespace/NaN key; assert the code **raises** (fail-closed), not scores it as 0/blank. | BUG-002 |
| D3 | **Validator shares the production blind spot** — the `validate_*.py` recomputes with the *same* fallback/assumption as the code it checks, so it can never catch that class of bug. | The validator asserts **independently**. It must fail on a defect the production code tolerates. Don't import the production fallback into the check. | A defect the generator swallows (see D2) must turn the validator **red**. If it stays green, the validator is complicit. | BUG-002 |
| D4 | **Non-idempotent generator** — the script reads the sheet/file it then overwrites, so a second run double-applies (re-scales values, re-appends audit rows) and there's no re-run guard. | Read the **immutable baseline** (a separate source sheet / committed constants), never the mutable output. Guard double-apply (a `basis`/state flag, or `--force`). Upsert audit rows by key; don't blind-append. A "`git restore` first" README note is not a guard. | Run the generator **twice** on a temp copy; assert byte/row-count/value identity with one run, or a clean non-zero "already applied" refusal. | BUG-003 |
| D5 | **Implementation contradicts its documented assumption** — the doc says one thing (e.g. factors estimated *independently*) but the code does another (multiplies them, compounding). | Pin the intended semantics with a test **before** anyone reuses the logic. Logic used in >1 place (back-test + runtime) lives in **one shared helper**, not two copies that drift. | An input that triggers the combination (multi-condition row, overlapping flags) asserts the **chosen** rule, not the naive one. | BUG-004 |
| D6 | **As-of / "today"-coupled logic** — a partition, age, eligibility or window computed from `AS_OF`/`date.today()` with constants tuned to one snapshot. | Anything keyed on a reference date is a time-bomb: test it at a **future** date, not just today. Freeze `AS_OF` *and* its dependent constants together, or derive the constants. | Re-run the unit with `AS_OF` advanced past the next boundary (a minor turning 18, a school year rolling over); assert it still holds or fails loud. | BUG-001 |

When you write a new generator or validator, walk this table before writing the impl — each row is a
candidate adversarial test. When you *fix* one of these, the fix's failing-first test must come from the
matching "test that catches it" column.

## CI/CD shape

The CI pipeline enforces the parts of this skill that can be machine-checked. Three jobs run on every PR; all must pass.

1. **Spec-ID traceability check.** Grep all test files for `spec: F\d+\.ac\d+`. Cross-reference with the `ACn` list parsed from `FEATURES.md`. Fail if any test lacks a spec tag or any criterion lacks a test.
2. **Test-first commit guard.** Inspect the PR's commits: the first commit that adds a file under `test/` (or `**/tests/`) on this branch must precede the first commit that adds a production file. Catches the "tests added in a cleanup commit" anti-pattern.
3. **Per-story CI job.** For each story touched in the PR, run:
   - **Spec lint** — every `ACn` in the story is a single testable assertion (no "etc.", no "and so on", no `TODO`).
   - **Test run** — the story's test file(s) pass; no regression in the suite.
   - **Lint** — the project linter exits 0 with the pinned config.
   - **Review-checklist script** — greps for `TODO` without a linked issue, commented-out blocks, hardcoded secrets patterns, and missing `ATTRIBUTION.md` references per F1.

A single workflow runs all stories; per-story gates make bisection cheap.

## Human review checklist (gate before merge)

The CI jobs above are the machine gate. This is the human gate. Both must pass. Each row is fail-closed — if any row doesn't hold, the PR does not merge.

| Check | What "passes" looks like |
| --- | --- |
| **Spec is complete** | The story has Description, numbered `ACn` criteria, edge / adversarial cases, cross-references and Expected outcome. Every `ACn` is a single testable assertion. |
| **No code slop** | No commented-out blocks, no `TODO` without a linked issue, no dead helpers, no speculative config, no copy-pasted near-duplicates. Deletion beats addition. |
| **Lint clean** | Project linter (`ruff`, `eslint`, `tsc --noEmit`) exits 0 with the repo's pinned config. New linter suppressions are an explicit PR comment, not a silent pragma. |
| **Style consistency** | Naming, import order, error-handling shape and module layout match the surrounding code. New file matches the structure of the file next to it. |
| **Comments explain why, not what** | Every non-trivial comment names the reason — the rule it implements, the constraint it satisfies, the assumption it relies on. No restating the code. Comments added in a fix explain the fix, not the original bug. |
| **Tests cover the contract** | The three test classes (happy / edge / adversarial) exist, the failing-first commit is in the history, every `ACn` has at least one test referencing it via `spec: F#.ac#`, and no test references an `ACn` that doesn't exist. |
| **Fail-closed & idempotent** (generators/validators) | Walked against the D1–D6 defect table: no hard-coded count the data owns, no silent-default lookup on a must-be-total key, the validator asserts independently of the code, the generator is idempotent (or guards double-apply), documented assumptions are pinned by a test, and any `AS_OF`-coupled logic has a future-date test. |
| **No secrets / no PII** | No tokens, no real customer data, no real DOBs in committed fixtures. Synthetic only. |
| **License / attribution** | Open-Meteo and any other third-party data source has its attribution in the file or `ATTRIBUTION.md` (see F1). |
| **`FEATURES.md` updated** | The story's status moved to `Done` (or the next status) in the same PR. |

## Definition of done

A story is "done" — and mergeable — only when **all** of the following are true:

- The spec blocks in `FEATURES.md` are present and complete.
- The three (or more) failing tests exist in the PR's commit history **before** the production code.
- Every test now passes and carries a `spec: F#.ac#` tag pointing at a real `ACn`.
- The linter is clean.
- Every row of the human review checklist above has been ticked.
- Every CI machine check (traceability, test-first, per-story job) has passed.
- The matching `FEATURES.md` entry has its status updated.

If any of these is false, the PR is not ready. Do not bypass the gate "to ship faster" — the next story pays the cost.

## Anti-patterns to refuse in review

- "I'll add the spec after." No. Spec first.
- "I'll add tests after." No. Tests first, derived from the spec.
- "The test doesn't need a spec tag." Every test traces to an `ACn` — no exceptions.
- "The test passes on my machine." Reproduce in CI; if it doesn't run in CI, it doesn't pass.
- "It's a small change, lint can wait." Lint is part of the change.
- "I'll comment it later." Comment in the same commit.
- "This is a workaround." Workarounds get a `shortcut:` comment naming the limit and the trigger to upgrade.
- "We can update the spec in a follow-up PR." The spec is part of this PR — that's the whole point of SDD.
- "The validators pass, so it's correct." A green suite proves the snapshot it ran on, not the code. If the edge test is the happy case with different numbers, it isn't an edge test — reach for the D1–D6 table.
- "`.get(key, 0)` is fine, the key is always there." If it's always there, assert it and raise when it isn't. A default that can't legally fire is a silenced error waiting for new data (D2).
- "Just `git restore` the workbook before re-running." A human-memory step is not a guard. Make the generator idempotent or refuse the second run (D4).
