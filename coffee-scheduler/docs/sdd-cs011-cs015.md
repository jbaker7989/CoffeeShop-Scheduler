# Running the A2A schedule-draft workflow

[F7 and F12](../FEATURES.md) define the acceptance criteria. This document records the implementation and operator contract for [CS-011 and CS-015](./plans/CS-011-CS-015-agent-contracts-and-schedule-graph.md).

## Services and credentials

Run each service from the scheduler project directory using `python -m agents.serve <service> --port <port>`. Install the scheduler dependencies from `requirements.txt`. Install the Forecast worker from `agents/demand_forecast/requirements-agent.txt` in a separate Python 3.14 environment. ADK and the gateway require different FastAPI versions.

Each service binds to loopback. Place it behind its HTTPS ingress and set `A2A_ENDPOINT` to the externally configured `/a2a` URL. Discovery is available at `/.well-known/agent-card.json`. The `A2A_LOOPBACK_TEST=1` switch permits HTTP loopback endpoints for integration tests. Set ingress request deadlines and rate/concurrency limits for the intended traffic. Each service follows the [A2A 0.3 JSON-RPC profile](https://a2a-protocol.org/v0.3.0/specification/) and accepts `message/send` with one structured DataPart. Results are A2A Messages. The profile issues no task IDs and does not advertise streaming, push notifications or extended cards.

Each service needs `A2A_TRUSTED_ISSUER` and `A2A_TRUST_PUBLIC_KEY_FILE`. This is a dedicated RS256 trust relationship. The issuer must authenticate and authorize the caller before minting the service credential, including the caller's current tenant membership and operation. Issue claims `iss`, `sub`, `tenant_id`, `aud`, `scope`, `iat`, and `exp`. Use a single exact audience and skill scope, a stable subject per caller and an expiry no more than 120 seconds after issue. Service keys and browser credentials serve distinct audiences. On rotation, update the trust configuration and restart the receiver. The current profile accepts one public key per service.

| Service and audience | Scope | Payload |
| --- | --- | --- |
| `scheduler` | `generate_schedule` | `version: 1`, inclusive ISO `start` and `end`, optional UUID `idempotency_key`. |
| `demand_forecast` | `predict_demand` | `version: 1`, `start`, `end`, pinned `model_version`, `feature_version`, `taxonomy_version`, `input_revision`. |
| `labor_compliance` | `evaluate_proposal` | `version: 1`, `location_id`, `employee_id`, aware `start` and `end`, explicit `breaks` array of aware spans. A proposal that needs week or crew context returns a typed failure. |
| `staff_comms` | `preview_notification` | `version: 1`, `template_id: call_out_cover`, `location_id`, aware `starts_at`. Returns a preview. |

Scheduler, Forecast and Labor Compliance require `DATABASE_URL`. The Scheduler and Forecast services also require the same `SCHEDULE_FORECAST_MODEL`. The model must exist for the authenticated tenant and have a matching feature set and taxonomy. Scheduler delegates to the configured `A2A_FORECAST_ENDPOINT` with a fresh short-lived token signed using `A2A_FORECAST_PRIVATE_KEY_FILE` and `A2A_FORECAST_ISSUER`. Configure Forecast's trusted key and issuer for this delegation. The token grants exactly `predict_demand` for the verified tenant. Keep signer private files in restricted mounts.

Inventory, Prep and Loyalty have design cards. Payroll/HCM uses n8n under F16. CS-083 owns the future reconciled export and its approval checks. Staff Comms delivery and consent stay with F15.

## Operation and recovery

Send structured dates to run the graph without a model. Programmatic deployments can supply the optional `classify_intent` callable to `agents.serve.build_app`; it receives a LangChain-formatted intent prompt and may return `generate` or `clarify`. Dates remain required. Treat every other classification as clarification. Keep the callable time-bounded and do not place payroll, employee or tenant context in intent text.

The service defaults to a 90-second workflow budget (`SCHEDULE_BUDGET_SECONDS` accepts 2–120). Allow sufficient time for the existing solver policy and its model build. The Forecast call has a per-attempt cap and at most one transient retry. CP-SAT uses the remaining budget after model construction. Infeasibility carries the solver assumption groups. A deadline, unacceptable gap, incomplete forecast grid or missing rule evidence returns a typed error. No schedule is returned on a failed workflow.

The graph assembles the full authorized tenant store set from current canonical data. Version 1 supports the existing New York calendar, single-day through seven-day windows, whole-hour opening and whole-/half-hour closing. Ambiguous or nonexistent open hours and unsupported configurations fail closed. The graph prepares candidate-specific F10 facts, reuses the existing scheduler and reports its limited scope. Returned findings, including any omitted cost terms, are for draft review. Any legal verification or publishing workflow must independently perform its existing checks.

To retry after process or network failure, submit the same request and idempotency key under the same verified issuer, subject and tenant. A different request for the same key returns a conflict. A busy run can be retried after the active attempt releases or its lease expires. A fresh attempt validates the last committed checkpoint and rechecks the pinned forecast inputs. Tenant-scoped PostgreSQL writes reject stale attempts. A terminal draft is returned only after the checkpoint commits. Draft state retention is seven days and old runs are pruned on subsequent claims. Resume revalidates authorization through the issuer and tenant state; rotate credentials at their normal expiry. Correlate failures with the request ID and local stage timing. Tool state is withheld from LangSmith traces.

Local integration uses disposable migrated and seeded PostgreSQL with `CS015_TEST_DATABASE_URL` and the separate Forecast interpreter in `CS011_ADK_PYTHON`. The live test creates its own three-store week with synthetic model, roster and calendar records, then removes that tenant. The migration round trip requires a separate disposable database.
