"""Pure CS-022 reconciliation of live coverage rows and effective-dated labor rules."""
from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date
from typing import Literal
from urllib.parse import urlsplit

from .engine import BLOCKING_ENFORCEMENT, LAW_ID

LAW_MARKER = re.compile(r"\bLAW\b", re.IGNORECASE)
# Keep an entire ID-shaped token so the engine grammar cannot accept a partial ID.
CITATION_TOKEN = re.compile(r"(?<![\w-])[A-Z]{2,}-[\w-]*")
Condition = Literal[
    "missing_citation", "malformed_citation", "missing_rule", "invalid_coverage_validity",
    "date_gap", "overlapping_versions", "unverified_rule", "invalid_evidence", "enforcement_conflict",
]


@dataclass(frozen=True)
class DateRange:
    """Canonical PostgreSQL [lower, upper) dates, with None for an unbounded endpoint."""

    lower: date | None = None
    upper: date | None = None
    empty: bool = False

    @property
    def has_days(self) -> bool:
        return not self.empty and (self.lower is None or self.upper is None or self.lower < self.upper)

    def intersection(self, other: DateRange) -> DateRange:
        return DateRange(
            max((bound for bound in (self.lower, other.lower) if bound is not None), default=None),
            min((bound for bound in (self.upper, other.upper) if bound is not None), default=None),
            self.empty or other.empty,
        )


@dataclass(frozen=True)
class CoverageRule:
    tenant_id: str
    rule_id: str
    description: str | None
    value: str | None
    basis: str | None
    validity: DateRange | None


@dataclass(frozen=True)
class LaborRule:
    tenant_id: str
    rule_id: str
    validity: DateRange
    enforcement: str | None
    verification_status: str | None
    verified_on: date | None
    source_citation: str | None
    source_url: str | None


@dataclass(frozen=True)
class Finding:
    tenant_id: str
    coverage_rule_id: str
    labor_rule_id: str | None
    interval: DateRange | None
    condition: Condition


def _has_evidence(rule: LaborRule) -> bool:
    if type(rule.verified_on) is not date or not (rule.source_citation or "").strip():
        return False
    url = rule.source_url
    if not url or any(character.isspace() or ord(character) < 32 for character in url):
        return False
    try:
        parsed = urlsplit(url)
        port = parsed.port
        return (parsed.scheme in {"http", "https"} and bool(parsed.hostname)
                and parsed.username is None and port != 0)
    except ValueError:
        return False


def _range_key(interval: DateRange) -> tuple:
    return (interval.lower is not None, interval.lower, interval.upper is None, interval.upper)


def _version_findings(window: DateRange, versions: list[LaborRule]) -> Iterator[tuple[Condition, DateRange]]:
    # Check date geometry independently so an unverified version cannot hide a
    # separate date gap. Each version intersecting the window must also be verified.
    cursor = window.lower
    seen = False
    relevant = [(window.intersection(rule.validity), rule) for rule in versions]
    for interval, rule in sorted(relevant, key=lambda item: _range_key(item[0])):
        if not interval.has_days:
            continue
        if interval.lower is not None and (interval.lower > cursor if cursor is not None else not seen):
            yield "date_gap", DateRange(cursor, interval.lower)
        elif seen and (cursor is None or interval.lower is None or interval.lower < cursor):
            yield "overlapping_versions", interval.intersection(DateRange(upper=cursor))

        if rule.verification_status != "VERIFIED":
            yield "unverified_rule", interval
        elif not _has_evidence(rule):
            yield "invalid_evidence", interval
        if rule.enforcement not in BLOCKING_ENFORCEMENT:
            yield "enforcement_conflict", interval

        if not seen or cursor is not None and (interval.upper is None or interval.upper > cursor):
            cursor = interval.upper
        seen = True

    if not seen or (cursor is not None and (window.upper is None or cursor < window.upper)):
        yield "date_gap", DateRange(cursor, window.upper)


def reconcile(coverage_rows: Iterable[CoverageRule], labor_rows: Iterable[LaborRule]) -> list[Finding]:
    """Check every distinct citation on every LAW row, returning deterministic findings."""
    versions: dict[tuple[str, str], list[LaborRule]] = defaultdict(list)
    for rule in labor_rows:
        versions[rule.tenant_id, rule.rule_id].append(rule)

    findings: list[Finding] = []
    for coverage in coverage_rows:
        text = " ".join(value or "" for value in (coverage.description, coverage.value, coverage.basis))
        if not LAW_MARKER.search(text):
            continue
        window = coverage.validity
        tenant, coverage_id = coverage.tenant_id, coverage.rule_id
        if window is None or not window.has_days:
            findings.append(Finding(tenant, coverage_id, None, window, "invalid_coverage_validity"))

        tokens = set(CITATION_TOKEN.findall(text)) - {coverage_id}
        # Mixed-case hyphenated prose is common. Exact rule IDs use uppercase tokens.
        tokens = {token for token in tokens if token == token.upper()}
        cited = {token for token in tokens if LAW_ID.fullmatch(token)}
        if tokens - cited:
            findings.append(Finding(tenant, coverage_id, None, window, "malformed_citation"))
        elif not cited:
            findings.append(Finding(tenant, coverage_id, None, window, "missing_citation"))

        for rule_id in cited:
            rules = versions.get((tenant, rule_id))
            if not rules:
                findings.append(Finding(tenant, coverage_id, rule_id, window, "missing_rule"))
            elif window is not None and window.has_days:
                findings.extend(Finding(tenant, coverage_id, rule_id, interval, condition)
                                for condition, interval in _version_findings(window, rules))

    return sorted(findings, key=lambda item: (
        item.tenant_id, item.coverage_rule_id, item.labor_rule_id or "",
        _range_key(item.interval or DateRange()), item.condition,
    ))
