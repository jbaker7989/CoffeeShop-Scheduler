"""Pure database service functions for CS-034 Rules Admin.

The caller supplies a live psycopg connection.  Every operation establishes a
transaction-local RLS context so it is safe when the connection comes from a
transaction-pooling proxy.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import replace
from datetime import date, datetime
from zoneinfo import ZoneInfo

from psycopg import sql
from psycopg.rows import dict_row

from .engine import BLOCKING_ENFORCEMENT, CoverageConflict, Rule, reconcile_coverage
from . import engine


class RulesAdminError(Exception):
    """Base error suitable for an HTTP adapter to map."""


class TenantContextMissing(RulesAdminError): pass
class NotFound(RulesAdminError): pass
class Forbidden(RulesAdminError): pass
class Conflict(RulesAdminError): pass
class ValidationError(RulesAdminError): pass


class ReconciliationError(ValidationError):
    def __init__(self, conflicts):
        self.conflicts = conflicts
        super().__init__(json.dumps(conflicts, default=str))


EDITABLE = frozenset({
    "rule_category", "jurisdiction", "wage_order", "enforcement", "applies_age_min",
    "applies_age_max", "applies_school_status", "applies_day_type", "applies_season",
    "requires_employer_flag", "metric", "operator", "value_num", "value_time",
    "value_text", "unit", "consequence", "waivable_by_consent", "waiver_doc_required",
    "priority", "effective_from", "effective_to", "source_citation", "source_url",
    "verification_status", "verified_on", "notes",
})


def prepare_session(conn, tenant_id: str) -> None:
    """Apply the mandatory, transaction-local app role and tenant scope."""
    if not tenant_id:
        raise TenantContextMissing("tenant context missing")
    try:
        conn.execute("SET LOCAL ROLE app_rw")
        who = conn.execute("SELECT current_user").fetchone()[0]
        if who != "app_rw":
            raise TenantContextMissing("refusing database role that bypasses RLS")
        conn.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
    except RulesAdminError:
        raise
    except Exception as exc:
        raise TenantContextMissing(f"tenant context unavailable: {exc}") from exc


def _rows(conn, query, params=()):
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(query, params)
        return cur.fetchall()


def _tenant_from(filters=None, tenant_id=None, principal=None):
    value = tenant_id or (filters or {}).get("tenant_id") or (principal or {}).get("tenant_id")
    if not value:
        raise TenantContextMissing("tenant context missing")
    return str(value)


def _tenant_from_session(conn):
    value = conn.execute("SELECT current_setting('app.tenant_id', true)").fetchone()[0]
    if not value:
        raise TenantContextMissing("tenant context missing")
    return str(value)


def _etag(row):
    return hashlib.sha256(f"{row['rule_id']}:{row['_xmin']}:{row['effective_from']}".encode()).hexdigest()


def _rule_row(conn, rule_id, effective_from=None):
    query = "SELECT labor_rules.*, xmin::text AS _xmin FROM labor_rules WHERE rule_id=%s"
    params = [rule_id]
    if effective_from is not None:
        query += " AND effective_from=%s"
        params.append(effective_from)
    query += " ORDER BY effective_from DESC LIMIT 1"
    rows = _rows(conn, query, params)
    if not rows:
        raise NotFound("rule not found")
    row = rows[0]
    row["etag"] = _etag(row)
    return row


def _as_engine_rule(row):
    return Rule(
        rule_id=row["rule_id"], jurisdiction=row["jurisdiction"], enforcement=row["enforcement"],
        applies_age_min=row["applies_age_min"], applies_age_max=row["applies_age_max"],
        applies_school_status=row["applies_school_status"], applies_day_type=row["applies_day_type"],
        applies_season=row["applies_season"], requires_employer_flag=row["requires_employer_flag"],
        metric=row["metric"], operator=row["operator"], value_num=row["value_num"],
        value_text=row["value_text"], unit=row["unit"], consequence=row["consequence"],
        waivable_by_consent=bool(row["waivable_by_consent"]), waiver_doc_required=bool(row["waiver_doc_required"]),
        priority=row["priority"] or 0, effective_from=row["effective_from"], effective_to=row["effective_to"],
        verification_status=row["verification_status"] or "",
    )


def _coverage_rows(conn):
    return _rows(conn, "SELECT rule_id, description, value, unit, basis, validity FROM coverage_rules")


def _coverage_is_supported(validity, legal_versions):
    """Return whether verified legal versions cover a half-open coverage range.

    ``None`` bounds carry PostgreSQL daterange's unbounded semantics.  Normal
    legal/coverage overlap is not a conflict; only a hole in the coverage
    window is.
    """
    lower, upper = validity.lower, validity.upper
    # An unbounded coverage record means "always configured", not a claim
    # about a historical/future interval the rule table cannot bound.  It is
    # supported once it cites at least one verified legal version.
    if lower is None and upper is None:
        return any(rule.verification_status == "VERIFIED" for rule in legal_versions)
    intervals = sorted(
        ((rule.effective_from, rule.effective_to) for rule in legal_versions
         if rule.verification_status == "VERIFIED"),
        key=lambda interval: (interval[0] is not None, interval[0]),
    )
    cursor = lower
    for start, end in intervals:
        # Restrict the legal interval to the coverage interval.
        if lower is not None and (end is not None and end <= lower):
            continue
        if upper is not None and start is not None and start >= upper:
            break
        start = lower if start is None or (lower is not None and start < lower) else start
        end = upper if end is None or (upper is not None and end > upper) else end
        if end is not None and start >= end:
            continue
        if cursor is None:
            if start is not None:
                return False
        elif start > cursor:
            return False
        if end is None:
            return True
        if cursor is None or end > cursor:
            cursor = end
        if upper is not None and cursor >= upper:
            return True
    return cursor is None


def reconcile_for_admin(proposed, all_rows, coverage_rows):
    trial_rows = [proposed if r["rule_id"] == proposed["rule_id"] and r["effective_from"] == proposed["effective_from"] else r for r in all_rows]
    trial = [_as_engine_rule(r) for r in trial_rows]
    conflicts = []
    relevant_coverage = []
    for coverage in coverage_rows:
        text = " ".join(str(coverage.get(k) or "") for k in ("description", "value", "basis"))
        cited = [item for item in engine.LAW_ID.findall(text) if item != coverage["rule_id"]]
        if proposed["rule_id"] in cited:
            relevant_coverage.append(coverage)
    try:
        # The engine owns citation/enforcement semantics.  An edit is assessed
        # against only coverage rows that cite the rule being edited.
        reconcile_coverage(trial, relevant_coverage)
    except CoverageConflict as exc:
        conflicts.append({"conflict_type": "law_citation", "details": str(exc)})
    for coverage in relevant_coverage:
        text = " ".join(str(coverage.get(k) or "") for k in ("description", "value", "basis"))
        if "LAW" not in text.upper():
            continue
        cited = [item for item in engine.LAW_ID.findall(text) if item != coverage["rule_id"]]
        for item in cited:
            legal_versions = [r for r in trial if r.rule_id == item]
            if any(r.verification_status != "VERIFIED" for r in legal_versions):
                conflicts.append({"conflict_type": "LAW_citation_unverified", "coverage_rule_id": coverage["rule_id"], "rule_id": item})
            validity = coverage.get("validity")
            if item == proposed["rule_id"] and validity and not _coverage_is_supported(validity, legal_versions):
                conflicts.append({"conflict_type": "effective_date_gap", "coverage_rule_id": coverage["rule_id"], "rule_id": item})
    return conflicts


def _status(conn, row, all_rows=None, coverage=None):
    conflicts = reconcile_for_admin(row, all_rows or _rows(conn, "SELECT labor_rules.*, xmin::text AS _xmin FROM labor_rules"), coverage or _coverage_rows(conn))
    if any(c["conflict_type"] == "LAW_citation_unverified" for c in conflicts):
        return "unverified_law", conflicts
    return ("conflict" if conflicts else "clean"), conflicts


def list_rules(conn, filters):
    tenant = _tenant_from(filters)
    prepare_session(conn, tenant)
    clauses, params = [], []
    for field in ("verification_status", "enforcement", "rule_category"):
        if filters.get(field) is not None:
            clauses.append(sql.SQL("{} = %s").format(sql.Identifier(field)))
            params.append(filters[field])
    query = sql.SQL("SELECT labor_rules.*, xmin::text AS _xmin FROM labor_rules")
    if clauses:
        query += sql.SQL(" WHERE ") + sql.SQL(" AND ").join(clauses)
    query += sql.SQL(" ORDER BY rule_id, effective_from")
    rows = _rows(conn, query, params)
    coverage = _coverage_rows(conn)
    for row in rows:
        row["etag"] = _etag(row)
        row["reconciliation_status"], _ = _status(conn, row, rows, coverage)
        row.pop("_xmin", None)
    return rows


def get_rule(conn, rule_id, *, tenant_id=None, effective_from=None):
    tenant_id = tenant_id or _tenant_from_session(conn)
    prepare_session(conn, _tenant_from(tenant_id=tenant_id))
    row = _rule_row(conn, rule_id, effective_from)
    all_rows = _rows(conn, "SELECT labor_rules.*, xmin::text AS _xmin FROM labor_rules")
    status, warnings = _status(conn, row, all_rows)
    row["reconciliation_status"] = status
    row["reconciliation_warnings"] = warnings
    row["waiver_warnings"] = list_waivers(conn, rule_id, tenant_id=tenant_id, tz=None)
    row["source_url_valid"] = validate_source_url(row["source_url"]) if row.get("source_url") else None
    row.pop("_xmin", None)
    return row


def _has_capability(principal):
    return "rules_manager" in (principal or {}).get("capabilities", set())


def _verification_gate(before, after, changes):
    """Guard only transitions this edit introduces, never inherited row state."""
    touches_verification = bool({"enforcement", "verification_status"} & changes.keys())
    blocking_unverified = (
        after["enforcement"] in BLOCKING_ENFORCEMENT
        and after.get("verification_status") != "VERIFIED"
    )
    # A prior note cannot silently justify a newly binding rule.  A verified
    # source is deliberately tied to the proposed status, not string presence.
    verified_source = (
        after.get("verification_status") == "VERIFIED"
        and bool(after.get("source_citation"))
        and bool(after.get("source_url"))
    )
    new_rationale = "notes" in changes and bool(str(changes.get("notes") or "").strip())
    if touches_verification and blocking_unverified and not (verified_source or new_rationale):
        raise ValidationError("design decision required for unverified LAW rule.")
    if (
        "verification_status" in changes
        and after.get("verification_status") == "VERIFY"
        and after["enforcement"] not in BLOCKING_ENFORCEMENT
        and not verified_source
    ):
        raise ValidationError("rule not yet verified; verify in CS-021 first.")


def patch_rule(conn, rule_id, changes, *, if_match, principal, today=None, effective_from=None):
    if not _has_capability(principal):
        raise Forbidden("rules_manager capability required")
    tenant = _tenant_from(principal=principal)
    prepare_session(conn, tenant)
    before = _rule_row(conn, rule_id, effective_from)
    if if_match != before["etag"]:
        raise Conflict("etag does not match current rule")
    unknown = set(changes) - EDITABLE
    if unknown:
        raise ValidationError(f"fields are not editable: {sorted(unknown)}")
    after = {k: v for k, v in before.items() if k not in {"_xmin", "etag"}}
    after.update(changes)
    _verification_gate(before, after, changes)
    all_rows = _rows(conn, "SELECT labor_rules.*, xmin::text AS _xmin FROM labor_rules")
    conflicts = reconcile_for_admin(after, all_rows, _coverage_rows(conn))
    if conflicts:
        raise ReconciliationError(conflicts)
    changed = {k: v for k, v in changes.items() if before[k] != v}
    if not changed:
        return {"matched": True, "modified": False, "etag": before["etag"], "source_url_valid": validate_source_url(before["source_url"]) if before.get("source_url") else None}
    assignment = sql.SQL(", ").join(sql.SQL("{} = %s").format(sql.Identifier(k)) for k in changed)
    conn.execute(sql.SQL("UPDATE labor_rules SET {} WHERE rule_id=%s AND effective_from=%s").format(assignment), [*changed.values(), rule_id, before["effective_from"]])
    source_valid = validate_source_url(after["source_url"]) if "source_url" in changes and after.get("source_url") else None
    audit_after = dict(after)
    if "source_url" in changes and not source_valid:
        audit_after["source_url_valid"] = False
        audit_after["source_url_audit"] = "source URL unverified; manager attested manually."
    notes = json.dumps({"action": "rule_edit", "before": {k: str(v) if isinstance(v, (date, datetime)) else v for k, v in before.items() if k not in {"_xmin", "etag"}}, "after": {k: str(v) if isinstance(v, (date, datetime)) else v for k, v in audit_after.items()}, "manager_id": str(principal.get("id"))}, default=str)
    conn.execute("""INSERT INTO rule_eval_log (tenant_id, schedule_id, shift_id, employee_id, rule_id, result, premium_cost_usd, agent, notes)
                    VALUES (%s, NULL, NULL, NULL, %s, 'PASS', 0, 'rules-admin-agent', %s)""", (tenant, rule_id, notes))
    fresh = _rule_row(conn, rule_id, after["effective_from"])
    return {"matched": True, "modified": True, "etag": fresh["etag"], "source_url_valid": source_valid}


def list_waivers(conn, rule_id, *, tenant_id=None, today=None, tz=None):
    tenant = _tenant_from(tenant_id=tenant_id) if tenant_id else _tenant_from_session(conn)
    prepare_session(conn, tenant)
    if today is None:
        zone = ZoneInfo(tz) if tz else ZoneInfo(_rows(conn, "SELECT timezone FROM tenants WHERE id=%s", (tenant,))[0]["timezone"])
        today = datetime.now(zone).date()
    rows = _rows(conn, "SELECT waiver_id, employee_id, rule_id, valid_from, valid_to, doc_reference, notes FROM consent_waivers WHERE rule_id=%s ORDER BY waiver_id", (rule_id,))
    for row in rows:
        end = row["valid_to"]
        row["expired"] = end is not None and today >= end
        row["active"] = row["valid_from"] <= today and not row["expired"]
        row["days_until_expiry"] = (end - today).days if end else None
        if row["expired"]:
            row["warning"] = f"Expired on {end}; rule re-evaluates without the waiver."
        elif end and 0 < row["days_until_expiry"] <= 7:
            row["warning"] = f"Action required: expires in {row['days_until_expiry']} day(s)"
        elif end and 7 < row["days_until_expiry"] <= 30:
            row["warning"] = "Expires within 30 days"
        else:
            row["warning"] = None
    return rows


class _NoCrossHostRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlparse(newurl).hostname != urllib.parse.urlparse(req.full_url).hostname:
            raise urllib.error.HTTPError(newurl, code, "cross-host redirect", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def validate_source_url(value):
    """Advisory SSRF-safe HTTPS probe; any failure is simply False."""
    if not value:
        return False
    try:
        parsed = urllib.parse.urlparse(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            return False
        for item in socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM):
            ip = ipaddress.ip_address(item[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_unspecified:
                return False
        opener = urllib.request.build_opener(_NoCrossHostRedirect)
        request = urllib.request.Request(value, method="HEAD")
        with opener.open(request, timeout=3) as response:
            return 200 <= response.status < 300
    except Exception:
        return False


def export_csv(conn, *, tenant_id=None):
    tenant = _tenant_from(tenant_id=tenant_id) if tenant_id else _tenant_from_session(conn)
    prepare_session(conn, tenant)
    with conn.cursor() as cur:
        with cur.copy("COPY (SELECT * FROM labor_rules) TO STDOUT WITH (FORMAT csv, HEADER true)") as copy:
            return b"".join(copy).decode()
