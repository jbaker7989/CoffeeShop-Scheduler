"""Read-only F10 evaluation exposed to authenticated agent clients."""
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, PositiveInt, ValidationError

from agents.a2a_transport import AgentError, ServiceIdentity
from compliance.engine import Break, ComplianceError, Proposal, evaluate, load_scoped_snapshot
from data_access.session import tenant_session


class SpanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    start: str
    end: str

    def span(self) -> tuple[datetime, datetime]:
        result = []
        for value in (self.start, self.end):
            if len(value) > 40 or "T" not in value:
                raise ValueError
            instant = datetime.fromisoformat(value)
            if instant.utcoffset() is None:
                raise ValueError
            result.append(instant.astimezone(UTC))
        if result[0] >= result[1]:
            raise ValueError
        return result[0], result[1]


class EvaluationRequest(SpanRequest):
    version: Literal[1]
    location_id: PositiveInt
    employee_id: PositiveInt
    breaks: list[SpanRequest]


def evaluate_proposal(dsn: str, identity: ServiceIdentity, data: dict) -> dict:
    try:
        request = EvaluationRequest.model_validate(data)
        if type(data["version"]) is not int or len(request.breaks) > 16:
            raise ValueError
        start, end = request.span()
        breaks = [Break(*item.span()) for item in request.breaks]
        if (end - start > timedelta(hours=24)
                or any(break_.start < start or break_.end > end for break_ in breaks)
                or any(left.end > right.start for left, right in zip(breaks, breaks[1:], strict=False))):
            raise ValueError
    except (ValidationError, ValueError, OverflowError):
        raise AgentError("invalid_proposal") from None

    try:
        with tenant_session(dsn, role="app_ro", tenant_id=identity.tenant_id, repeatable_read=True) as conn:
            conn.execute("SET LOCAL statement_timeout = '10s'")
            if not conn.execute(
                "SELECT 1 FROM tenants WHERE id = %s AND status IN ('active', 'trial')", (identity.tenant_id,)
            ).fetchone():
                raise AgentError("forbidden")
            employee = conn.execute(
                "SELECT role FROM employees WHERE employee_id = %s", (request.employee_id,)
            ).fetchone()
            store = conn.execute(
                "SELECT 1 FROM stores WHERE location_id = %s", (request.location_id,)
            ).fetchone()
            if employee is None or store is None:
                raise AgentError("proposal_inputs_unavailable")
            proposal = Proposal(str(identity.tenant_id), request.location_id, request.employee_id,
                                employee[0], start, end, breaks)
            result = evaluate(proposal, snapshot=load_scoped_snapshot(conn, proposal))
    except ComplianceError:
        raise AgentError("compliance_unavailable") from None

    return {
        "version": 1,
        "scope": "proposal",
        "claims_legal_accuracy": result.claims_legal_accuracy,
        "binding": [row.rule_id for row in result.binding],
        "results": [{"rule_id": row.rule_id, "result": row.result, "verification_status": row.verification_status,
                     "premium_cost_usd": str(row.premium_cost_usd) if row.premium_cost_usd is not None else None}
                    for row in result.results],
    }
