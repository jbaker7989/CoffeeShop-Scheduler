# CS-010 — Agent conventions and reusable skills

## Goal

Complete the partially scaffolded project and agent instructions so a contributor or coding agent can
discover where work belongs, how to run the right checks, and which established contract governs a task.
CS-010 is documentation and a lightweight documentation-contract test; it does not change runtime policy,
agent behavior, dependencies, or customer data.

## Current state and gap

- The project root has `AGENTS.md`, but its test command and security guidance need to match current
  configuration and gated tests.
- Four runtime agent `AGENTS.md` files exist; three are empty stubs and the scheduler stub is too brief to
  distinguish ownership and boundaries.
- The five skills exist, but labor-rule evaluation, sales/weather joins, and A2A authoring are titles rather
  than usable procedures. The roster and calibration skills already contain substantial, project-specific
  safeguards; preserve and review them rather than replacing them with generic text.
- `requirements.txt`, `ruff.toml`, `test/SKILL.md`, the root `.gitignore`, and `data/README.md` already own
  dependency, lint, test, and data-handling facts. Documentation must point at these canonical sources
  instead of copying mutable settings.

## Scope and explicit non-goals

In scope: refine F6's testable acceptance criteria; create this plan; complete root/agent instructions and
the five reusable skills; add a stdlib-only offline test that checks required inventory, minimum
actionability, key policy anchors, and relative Markdown links; update story status after verification.

Out of scope: authoring CS-011 agent cards, choosing or implementing a Payroll/HCM runtime, changing
dependency pins or adding a packaging tool, changing domain rules, changing the test policy, modifying other
verticals, or rewriting unrelated documentation. CS-011 consumes the `agent-card-authoring` skill.

## Documents and ownership

| Path | Owns | Must link to |
| --- | --- | --- |
| `AGENTS.md` | Project-wide stack/layer boundaries, data policy, canonical checks, fail-closed conventions | `test/SKILL.md`, `data/README.md`, `requirements.txt`, `ruff.toml` |
| `agents/scheduler/AGENTS.md` | Schedule graph and solver boundary; compliance and forecast inputs are delegated/contracted | `skills/labor-rule-evaluation/SKILL.md` where applicable, solver docs |
| `agents/labor_compliance/AGENTS.md` | Authoritative rule evaluation, evidence, and legal-claim limits | `skills/labor-rule-evaluation/SKILL.md`, compliance engine and F21 docs |
| `agents/demand_forecast/AGENTS.md` | Feature/model/inference ownership and forecast contract | forecast contract, `skills/sales-weather-join/SKILL.md`, calibration skill |
| `agents/staff_comms/AGENTS.md` | Staff-facing communication, consent/privacy, and no schedule-authoring authority | communication contract and shared data policy |
| `skills/labor-rule-evaluation/SKILL.md` | Repeatable inputs → resolution → evidence → failure procedure | compliance engine, rule contracts, F21/CS-021 |
| `skills/synthetic-roster-generation/SKILL.md` | Synthetic-only generation and validation invariants | generators, validators, `test/SKILL.md` |
| `skills/sales-weather-join/SKILL.md` | Source validation, join keys, units, missingness, attribution, and limits | data sources, join builder, validation report |
| `skills/standards-calibration/SKILL.md` | Calibration baseline, acceptance gate, audit and idempotency | calibrator, validator, `Calibration_Log`, test skill |
| `skills/agent-card-authoring/SKILL.md` | A2A card authoring, security/versioning and validation procedure | existing card schema/examples and CS-011 |

Agent-specific documents must state ownership and non-ownership and link to shared procedures. The shared
skill remains the source for its procedure; agent docs may summarize the boundary but must not clone the
steps or redefine legal, forecast, solver, security, or data policy.

## Implementation sequence (spec → tests → docs → review)

1. **Specify:** add F6 ACs, edge/adversarial cases, cross-references, and expected outcome to `FEATURES.md`.
   Confirm each AC can be asserted by an automated check or a direct review item.
2. **Test first:** add `test/test_cs010_agent_guidance.py` using only the standard library. Map tests to
   F6 ACs. Assert the complete path inventory exists, files are substantive and have the required sections,
   canonical policy anchors are present, and every local Markdown link resolves. Run it to establish a
   red result against the current stubs before writing guidance.
3. **Root policy:** revise the root instructions to state the real project commands and where to find the
   canonical test/lint/data policy. Clearly mark DB/Docker/telemetry integration gates and avoid declaring
   a skipped integration successful.
4. **Agent boundaries:** author four concise role documents. Define owned inputs/outputs, forbidden duplicate
   decisions (compliance does not live in the solver/LLM; forecasts do not become schedules; comms do not
   alter canonical schedules), and authoritative references.
5. **Reusable procedures:** make each skill usable by an unfamiliar contributor. Keep roster/calibration
   safeguards; add missing input validation, expected outputs, failure handling, and checks to the three
   stubs. Do not state legal conclusions beyond the verified rules source or invent runtime capabilities.
6. **Red/green review:** run the focused contract test, verify its tests fail if a required file or a link is
   removed, then run it green. Run the project's full unittest discovery and Ruff on changed Python files.
   Review every factual command/path against the actual tree/configuration and read all changed Markdown.
7. **Close:** update F6 and CS-010 to Done only after all checks pass. Preserve other backlog and strategy
   changes in the shared worktree; stage only CS-010 paths. Commit logical spec/test/docs units, then merge
   the completed story branch into `main` only if the worktree/branch state permits a clean, non-destructive
   local merge.

## Documentation-contract test design

- **Inventory:** every required AGENTS/SKILL path exists and is non-empty.
- **Actionability:** root and agent docs contain their required headings/anchors; every skill has procedure,
  validation, or safeguards beyond its title.
- **Policy accuracy:** root points to actual test, data, dependency, and lint authorities; commands are
  executable as written from the stated working directory.
- **Role boundaries:** agent documents contain owner and non-owner sections and the cross-component
  constraints listed above.
- **Links:** parse local Markdown link targets, discard URL/anchor-only links, resolve relative to the source
  document, and fail on missing files. Do not follow or require network links.
- **Safety review:** manually confirm no real credentials or PII, no directive to commit secrets, and no
  examples that imply fixture data is a production source of truth.

## Bug and edge-case controls

| Risk | Control / evidence |
| --- | --- |
| Instructions drift from code/config | Link canonical source files; automated local-link test; manual command/config verification |
| Role docs duplicate or conflict | One shared skill per procedure; agent docs state owner/non-owner and reference it |
| Empty file passes a file-exists check | Minimum meaningful sections/anchors and nontrivial content checks |
| Link parser misses a broken reference | Test relative paths, inline links and reference links used by the docs; review all changed links |
| A test passes through network access or optional dependencies | Use stdlib only and offline filesystem reads |
| Incorrect claim about a gated test | Root guidance distinguishes mandatory discovery from opt-in DB/Docker/telemetry checks |
| Legal/PII/security guidance becomes policy | Point to source rule/contracts and data policy; do not invent requirements or expose real examples |
| Existing good safeguards are lost | Review and retain roster/calibration content; minimal edits to already useful material |
| Unrelated work enters commit | Stage exact CS-010 paths; inspect staged diff before each commit |

## Verification and completion gate

From `coffee-scheduler/`:

```sh
python3 -m unittest test.test_cs010_agent_guidance -v
python3 -m unittest discover -s test
ruff check --config ruff.toml test/test_cs010_agent_guidance.py
git diff --check
```

Also inspect local Markdown link targets and confirm no secrets/PII were added. Report suite pass/fail/skip
counts distinctly. No runtime test is needed because this story changes instructions only. CS-010 is Done
only when every F6 AC is evidenced, the documentation test and full project suite pass, and changed-file
lint/checks pass.

## Merge and rollback

Keep commits limited to F6 specification, CS-010 tests, and CS-010 documentation/status. Merge only after
reviewing the final diff and confirming no unrelated working-tree edits would be swept into the merge. If
any claim is unsupported, revert that claim or replace it with a link to the actual source; do not loosen
the test to accommodate stale or vague instructions.
