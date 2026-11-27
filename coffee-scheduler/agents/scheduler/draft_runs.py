"""Tenant-scoped stage checkpoints and fenced run ownership."""
import json
from dataclasses import dataclass
from hashlib import sha256
from uuid import UUID, uuid4

from agents.a2a_transport import MAX_RESPONSE_BYTES, AgentError, ServiceIdentity
from data_access.repository import CheckpointRepo
from data_access.session import tenant_session


@dataclass(frozen=True, slots=True)
class RunClaim:
    run_id: UUID
    attempt: UUID | None
    stage: str
    state: dict


class DraftRuns:
    def __init__(self, dsn: str):
        self.dsn = dsn

    def _session(self, identity):
        return tenant_session(self.dsn, role="app_rw", tenant_id=identity.tenant_id)

    def claim(self, identity: ServiceIdentity, run_id: UUID, request: dict, *, lease_seconds: int) -> RunClaim:
        fingerprint = sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
        tenant = identity.tenant_id
        with self._session(identity) as conn:
            conn.execute("SET LOCAL lock_timeout = '3s'")
            conn.execute("SET LOCAL statement_timeout = '10s'")
            if not conn.execute(
                "SELECT 1 FROM tenants WHERE id = %s AND status IN ('active', 'trial')", (tenant,)
            ).fetchone():
                raise AgentError("forbidden")
            # Serialize claims per tenant. Different tenants can run independently.
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                         (f"schedule-draft:{tenant}",))
            self._expire(conn, tenant)
            row = conn.execute("""
                SELECT issuer, subject, request_hash, stage, attempt IS NOT NULL
                       AND lease_until > clock_timestamp()
                FROM schedule_draft_runs WHERE tenant_id = %s AND id = %s FOR UPDATE
                """, (tenant, run_id)).fetchone()
            if row and row[:2] != (identity.issuer, identity.subject):
                raise AgentError("draft_unavailable")
            if row and row[2] != fingerprint:
                raise AgentError("draft_conflict")
            stage = row[3] if row else "start"
            state = self._load(conn, tenant, run_id, stage, fingerprint)
            if stage == "done":
                return RunClaim(run_id, None, stage, state)
            if conn.execute("""
                SELECT 1 FROM schedule_draft_runs
                WHERE tenant_id = %s AND attempt IS NOT NULL AND lease_until > clock_timestamp()
                LIMIT 1
                """, (tenant,)).fetchone():
                raise AgentError("draft_busy", transient=True)
            attempt = uuid4()
            conn.execute("""
                INSERT INTO schedule_draft_runs
                    (tenant_id, id, issuer, subject, request_hash, attempt, lease_until, stage)
                VALUES (%s, %s, %s, %s, %s, %s, clock_timestamp() + %s * interval '1 second', 'start')
                ON CONFLICT (tenant_id, id) DO UPDATE
                    SET attempt = EXCLUDED.attempt, lease_until = EXCLUDED.lease_until,
                        updated_at = clock_timestamp()
                """, (tenant, run_id, identity.issuer, identity.subject, fingerprint, attempt, lease_seconds))
            return RunClaim(run_id, attempt, stage, state)

    def _expire(self, conn, tenant):
        # Remove only this workflow's runs older than its replay period.
        conn.execute("""
            WITH expired AS (
                DELETE FROM schedule_draft_runs WHERE tenant_id = %s
                    AND updated_at < clock_timestamp() - interval '7 days'
                    AND lease_until <= clock_timestamp()
                RETURNING id
            )
            DELETE FROM langgraph_checkpoints AS c USING expired
              WHERE c.tenant_id = %s AND c.agent_id = expired.id AND c.checkpoint->>'workflow' = 'schedule-draft'
            """, (tenant, tenant))

    def _load(self, conn, tenant, run_id, stage, fingerprint):
        if stage == "start":
            return {}
        row = CheckpointRepo(conn).get_latest(tenant_id=tenant, agent_id=run_id)
        data = row["checkpoint"] if row else None
        if (not isinstance(data, dict) or data.get("workflow") != "schedule-draft"
                or type(data.get("version")) is not int or data["version"] != 1
                or data.get("stage") != stage or data.get("request_hash") != fingerprint
                or not isinstance(data.get("state"), dict)):
            raise AgentError("invalid_draft_checkpoint")
        state = data["state"]
        required = {"gather": {"pins"}, "forecast": {"pins", "forecast"}, "done": {"result"}}[stage]
        if set(state) != required or not all(isinstance(state[key], dict) for key in required):
            raise AgentError("invalid_draft_checkpoint")
        if stage == "done" and (state["result"].get("run_id") != str(run_id)
                                or state["result"].get("status") != "draft"
                                or state["result"].get("version") != 1):
            raise AgentError("invalid_draft_checkpoint")
        return state

    def save(self, identity: ServiceIdentity, claim: RunClaim, stage: str, state: dict):
        with self._session(identity) as conn:
            conn.execute("SET LOCAL statement_timeout = '10s'")
            row = conn.execute("""
                UPDATE schedule_draft_runs
                SET stage = %s, updated_at = clock_timestamp(),
                    attempt = CASE WHEN %s = 'done' THEN NULL ELSE attempt END
                WHERE tenant_id = %s AND id = %s AND attempt = %s
                  AND lease_until > clock_timestamp()
                  AND EXISTS (
                      SELECT 1 FROM tenants WHERE id = %s AND status IN ('active', 'trial')
                  )
                  AND CASE %s WHEN 'gather' THEN stage IN ('start', 'gather', 'forecast')
                              WHEN 'forecast' THEN stage = 'gather'
                              WHEN 'done' THEN stage = 'forecast' ELSE FALSE END
                RETURNING request_hash
                """, (stage, stage, identity.tenant_id, claim.run_id, claim.attempt,
                      identity.tenant_id, stage)).fetchone()
            if row is None:
                raise AgentError("draft_claim_lost")
            payload = {"version": 1, "workflow": "schedule-draft", "stage": stage,
                       "request_hash": row[0], "state": state}
            if len(json.dumps(payload, allow_nan=False).encode()) > MAX_RESPONSE_BYTES:
                raise AgentError("draft_checkpoint_too_large")
            CheckpointRepo(conn).save(tenant_id=identity.tenant_id, agent_id=claim.run_id, checkpoint=payload)
            # Only the most recent committed stage is needed to resume a linear run.
            conn.execute("""
                DELETE FROM langgraph_checkpoints WHERE tenant_id = %s AND agent_id = %s
                    AND checkpoint->>'workflow' = 'schedule-draft'
                    AND seq < (SELECT max(seq) FROM langgraph_checkpoints WHERE tenant_id = %s AND agent_id = %s)
                """, (identity.tenant_id, claim.run_id, identity.tenant_id, claim.run_id))

    def release(self, identity: ServiceIdentity, claim: RunClaim):
        with self._session(identity) as conn:
            conn.execute("SET LOCAL statement_timeout = '10s'")
            conn.execute("""
                UPDATE schedule_draft_runs SET attempt = NULL WHERE tenant_id = %s AND id = %s AND attempt = %s
                """, (identity.tenant_id, claim.run_id, claim.attempt))
