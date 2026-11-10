# Skill: test

Test-Driven Development is part of our process. Every story ships with failing tests written first and a passing test suite written last.

## When this skill applies

Every entry in `FEATURES.md` (F1–F29), every bug fix, and every refactor. No story is "done" until the tests, the lint, and the review checklist below all pass.

## Workflow

1. **Read the story.** Open the matching entry in `FEATURES.md`. The "Expected outcome" is the contract the tests must verify.
2. **Write three failing tests before writing production code.** Three is a floor, not a ceiling — use more if the story warrants it. Cover, at minimum:
   - **Happy path** — the canonical example from the story's "Expected outcome".
   - **One realistic edge case** — the boundary condition the story explicitly calls out (e.g. waiver expiry on the shift date, forecast quantile P70, solver infeasibility on a minor past curfew).
   - **One failure / adversarial case** — the input the system must reject, log, or fall over loud on (e.g. an unverified rule, a roster row missing `is_enrolled`, a forecast with `weather_bucket = None`).
3. **Run the tests.** They **must fail** for the documented reason. If they pass before any production code exists, the tests are wrong — rewrite them. Commit the failing tests on their own.
4. **Write the minimum production code to turn them green.** Per the Ponytail principle: no extra features, no abstraction nobody asked for. Use the helpers and patterns the codebase already has.
5. **Run the full suite + linter.** All three (or more) tests now pass. No regressions in adjacent tests. Linter is clean.
6. **Review against the checklist below.** Fix anything it flags before opening the PR.
7. **Merge only when every check passes.** A PR with failing tests, lint errors, or unchecked review items does not merge.

## Tests live where?

- Project-level tests: `coffee-scheduler/test/` (this directory) or a `tests/` subfolder per agent under `agents/<name>/tests/` — match the convention the agent's neighbours use.
- File naming: `test_<unit>_<behaviour>.py` or `test_<feature>.py`. One test file per story is fine.
- Fixtures and synthetic data (the `coffee_shop_*.xlsx` workbooks, the weather join) are fixtures, not tests.

## Review checklist (gate before merge)

Run through this on every PR. Each item is a fail-closed gate — if any row doesn't hold, the PR does not merge.

| Check | What "passes" looks like |
| --- | --- |
| **No code slop** | No commented-out blocks, no `TODO` without a linked issue, no dead helpers, no speculative config, no copy-pasted near-duplicates. Deletion beats addition. |
| **Lint clean** | Project linter (e.g. `ruff`, `eslint`, `tsc --noEmit`) exits 0 with the repo's pinned config. New linter suppressions are an explicit PR comment, not a silent pragma. |
| **Style consistency** | Naming, import order, error-handling shape, and module layout match the surrounding code. New file matches the structure of the file next to it. |
| **Comments explain why, not what** | Every non-trivial comment names the reason — the rule it implements, the constraint it satisfies, the assumption it relies on. No restating the code. Comments added in a fix explain the fix, not the original bug. |
| **Tests cover the contract** | The three test classes (happy / edge / adversarial) exist, the failing-first commit is in the history, and every `Expected outcome` line in the story has at least one assertion pointing at it. |
| **No secrets / no PII** | No tokens, no real customer data, no real DOBs in committed fixtures. Synthetic only. |
| **License / attribution** | Open-Meteo and any other third-party data source has its attribution in the file or `ATTRIBUTION.md` (see F1). |

## Definition of done

A story is "done" — and mergeable — only when **all** of the following are true:

- The three (or more) failing tests exist in the PR's commit history before the production code.
- Every test now passes.
- The linter is clean.
- Every row of the review checklist above has been ticked.
- The matching `FEATURES.md` entry has its status updated to `Done` (or the next status in its lifecycle).

If any of these is false, the PR is not ready. Do not bypass the gate "to ship faster" — the next story pays the cost.

## Anti-patterns to refuse in review

- "I'll add tests after." No. Tests first.
- "The test passes on my machine." Reproduce in CI; if it doesn't run in CI, it doesn't pass.
- "It's a small change, lint can wait." Lint is part of the change.
- "I'll comment it later." Comment in the same commit.
- "This is a workaround." Workarounds get a `shortcut:` comment naming the limit and the trigger to upgrade.
