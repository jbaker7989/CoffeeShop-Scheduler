# Skill: labor-rule-evaluation

Use this procedure when implementing or explaining a proposed shift's legal-rule result. The compliance
engine and verified rule records are authoritative; this skill is a workflow guide, not a separate source of
law. Never let the solver or an LLM substitute its own legal decision.

## Procedure

1. Validate the shift interval, employee, location, jurisdiction, and effective evaluation date against
   canonical records. Reject missing or contradictory identity/configuration rather than selecting defaults.
2. Load applicable rule versions and their jurisdiction, scope, effective interval, status, and provenance.
   Use the rule engine's documented half-open/effective-date semantics; do not compare against today's date
   when the shift date is the question.
3. Resolve overlaps using the engine's strictest-wins semantics. Apply any waiver only when the rule allows
   it and the waiver belongs to the canonical employee/rule and is valid on the shift date.
4. Evaluate minor restrictions using age on the proposed shift date and the applicable school calendar.
   Never persist a derived age as identity data.
5. Return the engine's typed result and evidence: rule ids/versions, verdict, explanation, effective dates,
   and premium amount when applicable. Record both passes and failures through the supported audit path.
6. Keep `claims_legal_accuracy` false unless the verified-rule gate is satisfied; disclose unknown or
   unverified rules as such.

## Validation

- Pin each behavior with tests tagged to the owning acceptance criterion. Include ordinary, date-boundary,
  overlapping-rule, expired-waiver, minor-at-age-boundary, missing-calendar and unverified-rule cases.
- Check the implementation contract in `compliance/engine.py`, rule schemas and current `test/SKILL.md`;
  do not duplicate an algorithm in an agent wrapper.
- Verify audit writes for pass and fail and ensure a repository/audit failure cannot be reported as a
  successful evaluation.
- For a changed rule, verify effective dates, jurisdiction and citation against the authoritative rule
  source before marking it verified. Do not infer legal meaning from a title alone.

## Failure modes

- Missing employee/location/rule/calendar or unsupported jurisdiction: return a typed unavailable/unknown
  result that blocks a hard constraint; do not treat missing as compliant.
- Conflicting or overlapping rules: preserve engine strictest-wins behavior and include all applicable
  evidence; never choose the convenient result.
- Expired, foreign, or malformed waiver: ignore it as a waiver and evaluate the underlying rule.
- Unverified law: it can constrain safely when configured, but it cannot support a claim of verified legal
  accuracy.
- Audit or rule-store failure: fail the evaluation/request explicitly. Do not create an unaudited pass.
- A model-generated explanation disagrees with the engine: present the engine result and correct the
  explanation; never let prose override the verdict.
