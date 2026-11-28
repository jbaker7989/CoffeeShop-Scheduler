# Skill: agent-card-authoring

Use this procedure for A2A cards only when an agent is a real service boundary. A card describes the
service's externally supported contract; it does not create a runtime, authorize access by itself, or
replace application authentication and validation.

## Procedure

1. Confirm the agent is an independently addressable service. Internal graph nodes and n8n workflows do not
   automatically need A2A cards.
2. Inspect existing cards and repository schema/tests before editing. Preserve stable agent identity and
   increment the version according to the existing project convention for a breaking or compatible change.
3. Declare only implemented capabilities, supported input/output media, protocol/version, endpoint and
   authentication requirements. Describe authorization separately from transport authentication.
4. Keep tenant identity and sensitive-data handling in the authenticated service contract. Never place
   passwords, API keys, bearer tokens, private endpoints, real employee/customer data or secrets in a card.
5. Link each capability to the owning agent and typed contract. Mark placeholders as placeholders; do not
   advertise unimplemented operations as callable.
6. Validate JSON/schema, identity uniqueness, version, endpoint configuration and capability-contract
   consistency. Add an interoperability test before claiming a second-framework integration.

## Validation

- Parse each changed card as JSON and run the repository card/schema contract tests.
- Assert required fields, unique stable agent ids, supported versions, and that every advertised capability
  maps to a real operation/contract.
- Test missing/invalid auth metadata, unsupported protocol versions, duplicate ids and unconfigured remote
  endpoints. Secrets and tenant data must never appear in fixture cards.
- For a framework interoperability claim, run an end-to-end request against the actual two implementations;
  matching JSON files alone do not prove interoperability.
- Record unresolved service-boundary decisions (including Payroll/HCM) explicitly in the owning story, not
  as an undocumented assumption in a card.

## Failure modes

- No actual service boundary: do not create a misleading runtime card; document the workflow/internal-node
  ownership instead.
- Capability not implemented or contract missing: omit it or label the artifact as a non-callable placeholder.
- Invalid schema, duplicate identity, or version ambiguity: fail the card validation and do not publish it.
- Auth or endpoint configuration absent: fail closed; do not advertise unauthenticated access as a default.
- A card contains credentials or PII: remove it from the artifact and rotate exposed credentials if applicable;
  never preserve the secret in a test fixture or commit history.
