"""CS-045 / F45 — typed, pooled, role-aware data-access layer.

Agents call repository methods instead of writing SQL. Tenant isolation is enforced
by Postgres RLS (F60); this layer sets the session context (role + app.tenant_id,
transaction-local and parameterised) and exposes typed, parameterised reads/writes.
It never re-implements tenant filtering in Python and never builds SQL from values.

See coffee-scheduler/docs/sdd-cs045.md.
"""
from .session import open_session_context, tenant_session
from .tables import TableSpec, introspect, APPEND_ONLY, GLOBAL
from .repository import Repository, AppendOnlyError, CheckpointRepo

__all__ = [
    "open_session_context", "tenant_session",
    "TableSpec", "introspect", "APPEND_ONLY", "GLOBAL",
    "Repository", "AppendOnlyError", "CheckpointRepo",
]
