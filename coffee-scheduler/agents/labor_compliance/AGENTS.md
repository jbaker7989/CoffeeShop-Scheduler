# Labor-compliance agent

## Owns

- Resolve applicable effective-dated labor rules and provide the compliance engine's typed verdict,
  reason, premium, and rule evidence to the scheduler.
- Keep rule evaluation centralized in `compliance/engine.py`; record evaluations through its supported
  audit path.
- Distinguish verified legal rules from assumptions and report the configured legal-claim state accurately.

## Does not own

- Do not create or alter schedules, optimize staffing, or replace scheduler constraints.
- Do not infer that an unverified rule is safe to ignore. Unknown or incomplete hard-rule evidence must
  remain binding/fail closed according to the engine contract.
- Do not let a language model make legal conclusions or override a deterministic verdict.

## Inputs

- A proposed shift and canonical employee, location, calendar, waiver, and employer configuration records.
- Effective dates, jurisdiction and provenance from the rule source; current rule schemas and engine
  behavior are authoritative, not a copied summary in this document.

## Outputs

- A typed pass/fail/unknown result with applicable rule ids, reasons, effective dates, and premium details.
- An audit record for evaluations using the supported repository path; errors are explicit, not silent passes.

## Skills

- Follow [`skills/labor-rule-evaluation/SKILL.md`](../../skills/labor-rule-evaluation/SKILL.md) for the shared
  evaluation procedure.
- Follow [`../../test/SKILL.md`](../../test/SKILL.md) for specification traceability and adversarial tests.
- Use [`../../AGENTS.md`](../../AGENTS.md) for project-wide conventions and test commands.
