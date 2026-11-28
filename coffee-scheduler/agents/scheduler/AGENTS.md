# Scheduler agent

## Owns

- Assemble and run the scheduling graph and OR-Tools CP-SAT model in `agents/scheduler/`.
- Consume validated forecast, roster, operating-hours, coverage, and compliance inputs through the typed
  contracts and adapters documented by CS-016.
- Return solver status, assignments, objective evidence, and typed infeasibility/errors to the caller.

## Does not own

- Do not decide whether a shift is legal. `compliance/engine.py` and the labor-compliance agent own rule
  evaluation; use their verdicts and evidence as hard constraints.
- Do not generate or repair forecasts, invent employee/location facts, or let an LLM create assignments.
- Do not treat missing inputs, unknown rule results, or an unacceptable solver result as success.

## Inputs

- Typed solver inputs assembled by the scheduler adapters, including tenant-scoped canonical records.
- Contract-valid forecast rows (`contracts/forecast.py`) and explicit solver stop policy.
- Compliance verdicts and their rule/version evidence; unresolved hard rules remain binding.

## Outputs

- Deterministic schedule result or a typed no-incumbent, infeasible, invalid-input, or unacceptable-gap
  failure. Include solver status and quality evidence; never label an unproven solution as optimal.
- Preserve source identifiers needed to explain an assignment and audit later changes.

## Skills

- For rule semantics, read [`skills/labor-rule-evaluation/SKILL.md`](../../skills/labor-rule-evaluation/SKILL.md).
- For test-first, traceable changes, read [`../test/SKILL.md`](../../test/SKILL.md) from the project root (path
  from this agent directory: `../../test/SKILL.md`).
- Follow the canonical test commands in [`../../AGENTS.md`](../../AGENTS.md).
