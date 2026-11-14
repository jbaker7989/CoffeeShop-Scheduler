"""CS-013 / F10 labor-compliance engine.

The solver and the LLM call this. They do not compute legality themselves.
Unknown metrics, a missing tenant, and an incomplete proposal raise — they never
all-pass. US-FED rules always apply: the tenant jurisdiction string does not
list them, and dropping them would make minor-occupation rules invisible.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

AGENT = "labor-compliance-agent"
FEDERAL = "US-FED"
RESULTS = frozenset({"PASS", "FAIL", "PREMIUM", "WARN"})
BLOCKING_ENFORCEMENT = frozenset({"HARD", "PROCESS"})
# (metric, operator or "", unit or "") present in the committed Labor_Rules sheet.
SUPPORTED = frozenset({
    ("role_eligibility", "", "ROLE"),
    ("work_window", "BETWEEN", "TIME"),
    ("hours_paid_min", ">=", "HOURS"),
    ("process", "", "PROCESS"),
    ("certified_staff_on_site", ">=", "HEADCOUNT"),
    ("premium_amount", "", "USD"),
    ("rest_hours_between_shifts", ">=", "HOURS"),
    ("advance_notice_days", ">=", "DAYS"),
    ("rate", ">=", "USD"),
    ("rate", "", ""),
    ("break_minutes", ">=", "MINUTES"),
    ("hours_per_week", "<=", "HOURS"),
    ("hours_per_day", "<=", "HOURS"),
    ("days_per_week", "<=", "DAYS"),
    ("hours_per_week", "TRIGGER_WHEN_GT", "MULTIPLIER"),
    ("span_hours", "TRIGGER_WHEN_GT", "HOURS"),
})
ROLES = frozenset({"Barista", "Register", "Shift lead"})
WEEKLY_METRICS = frozenset({
    "hours_per_week", "hours_per_day", "days_per_week", "span_hours", "rest_hours_between_shifts",
})
LAW_ID = re.compile(r"\b[A-Z]{2,}(?:-[A-Z0-9]+)+\b")
FLAG = re.compile(r"^([A-Za-z_]+)=(TRUE|FALSE)$", re.I)
CLOCK = re.compile(r"(\d{1,2}:\d{2})")


class ComplianceError(Exception):
    pass


class ComplianceUnavailable(ComplianceError):
    pass


class ComplianceConfigError(ComplianceError):
    pass


class ShiftNotPersisted(ComplianceError):
    pass


class CoverageConflict(ComplianceError):
    pass


@dataclass(frozen=True)
class Break:
    start: datetime
    end: datetime


@dataclass(frozen=True)
class ShiftSpan:
    start: datetime
    end: datetime
    shift_id: str | None = None


@dataclass(frozen=True)
class CrewMember:
    employee_id: int
    food_protection_cert: bool
    name: str = ""


@dataclass
class Proposal:
    tenant_id: str
    location_id: int
    employee_id: int
    role: str
    start_at: datetime
    end_at: datetime
    breaks: list[Break]
    week_start: date | None = None
    week_shifts: list[ShiftSpan] | None = None
    week_complete: bool = False
    crew: list[CrewMember] | None = None
    crew_complete: bool = False
    call_out_reason: str | None = None
    requires_employee_to_find_cover: bool = False
    sent_home: bool = False
    posted_on: date | None = None
    schedule_change: bool = False
    shift_id: str | None = None
    schedule_id: str | None = None
    employer_id: str | None = None


@dataclass(frozen=True)
class Rule:
    rule_id: str
    jurisdiction: str
    enforcement: str
    applies_age_min: int | None
    applies_age_max: int | None
    applies_school_status: str | None
    applies_day_type: str | None
    applies_season: str | None
    requires_employer_flag: str | None
    metric: str
    operator: str | None
    value_num: Decimal | None
    value_text: str | None
    unit: str | None
    consequence: str | None
    waivable_by_consent: bool
    waiver_doc_required: bool
    priority: int
    effective_from: date
    effective_to: date | None
    verification_status: str


@dataclass(frozen=True)
class Waiver:
    waiver_id: str
    employee_id: int
    rule_id: str
    valid_from: date
    valid_to: date | None
    doc_reference: str | None


@dataclass(frozen=True)
class Employee:
    employee_id: int
    dob: date
    hourly_rate: Decimal
    is_enrolled: bool
    has_other_employer: bool | None
    food_protection_cert: bool


@dataclass(frozen=True)
class DayFacts:
    in_session: bool
    day_before_school_day: bool


@dataclass
class Snapshot:
    tenant_id: str
    timezone: str
    jurisdictions: tuple[str, ...]
    employer_flags: dict[str, bool]
    employee: Employee
    rules: tuple[Rule, ...]
    waivers: tuple[Waiver, ...]
    days: dict[date, DayFacts]
    labor_days: dict[int, date]
    wage_floor: Decimal | None


@dataclass(frozen=True)
class RuleResult:
    rule_id: str
    result: str
    premium_cost_usd: Decimal | None
    reason: str
    verification_status: str
    waived_by: str | None
    metric: str
    priority: int
    enforcement: str
    bound: Decimal | None = None


@dataclass(frozen=True)
class Evaluation:
    results: tuple[RuleResult, ...]
    binding: tuple[RuleResult, ...]
    claims_legal_accuracy: bool


@dataclass
class MemoryLog:
    shifts: set = field(default_factory=set)
    rows: list = field(default_factory=list)

    def shift_exists(self, tenant_id, shift_id) -> bool:
        return (str(tenant_id), str(shift_id)) in self.shifts

    def insert(self, row: dict) -> None:
        self.rows.append(row)

    def latest(self, tenant_id, shift_id, employee_id, rule_id):
        matched = [
            row for row in self.rows
            if row["tenant_id"] == str(tenant_id)
            and row["shift_id"] == shift_id
            and row["employee_id"] == employee_id
            and row["rule_id"] == rule_id
        ]
        return max(matched, key=lambda row: row["evaluated_at"]) if matched else None


def vocab_key(metric, operator, unit) -> tuple[str, str, str]:
    return (metric or "", operator or "", unit or "")


def evaluate(proposal: Proposal, *, snapshot: Snapshot | None = None, persist: bool = False,
             clock: datetime | None = None, dsn: str | None = None, log: MemoryLog | None = None) -> Evaluation:
    if proposal.breaks is None:
        raise ComplianceConfigError("breaks is required; pass [] when no break is scheduled")
    _validate_proposal_times(proposal)
    if snapshot is None:
        snapshot = load_snapshot(proposal, dsn=dsn)
    evaluation = judge(snapshot, proposal)
    if persist:
        when = clock or datetime.now(ZoneInfo("UTC"))
        _persist(proposal, evaluation, when, dsn=dsn, log=log)
    return evaluation


def reconcile_coverage(rules, coverage_rows) -> None:
    known = {rule.rule_id for rule in rules}
    for row in coverage_rows:
        text = " ".join(str(row.get(key) or "") for key in ("description", "value", "basis"))
        if not re.search(r"\bLAW\b", text, re.I):
            continue
        cited = [item for item in LAW_ID.findall(text) if item != row.get("rule_id")]
        if not cited:
            raise CoverageConflict(f"{row.get('rule_id')} is LAW but cites no labor rule")
        for rule_id in cited:
            if rule_id not in known:
                raise CoverageConflict(f"{row.get('rule_id')} cites missing rule {rule_id}")
            rule = next(item for item in rules if item.rule_id == rule_id)
            if rule.enforcement not in BLOCKING_ENFORCEMENT:
                raise CoverageConflict(
                    f"{row.get('rule_id')} is LAW but {rule_id} enforcement is {rule.enforcement}"
                )


def judge(snapshot: Snapshot, proposal: Proposal) -> Evaluation:
    if not snapshot.timezone or not snapshot.rules:
        raise ComplianceUnavailable("no timezone or no rules; refusing to treat the shift as legal")
    # Vocabulary must be understood before person-specific filters can hide a
    # binding rule, but historical and foreign-jurisdiction rows cannot bind.
    shift_day = proposal.start_at.astimezone(ZoneInfo(snapshot.timezone)).date()
    unknown = [
        rule.rule_id for rule in snapshot.rules
        if _in_force(rule, shift_day)
        and _jurisdiction_ok(rule, snapshot.jurisdictions)
        and vocab_key(rule.metric, rule.operator, rule.unit) not in SUPPORTED
    ]
    if unknown:
        raise ComplianceConfigError(f"unsupported rule vocabulary: {unknown}")
    if snapshot.employee.dob is None:
        raise ComplianceUnavailable(f"employee {proposal.employee_id} has no dob")
    zone = ZoneInfo(snapshot.timezone)
    if snapshot.employee.is_enrolled and shift_day not in snapshot.days:
        raise ComplianceUnavailable(
            f"enrolled employee {proposal.employee_id} has no school_calendar_days row for {shift_day}"
        )
    age = _age(snapshot.employee.dob, shift_day)
    applicable = []
    for rule in snapshot.rules:
        if not _in_force(rule, shift_day):
            continue
        if not _jurisdiction_ok(rule, snapshot.jurisdictions):
            continue
        if not _age_ok(rule, age):
            continue
        if not _flag_ok(rule, snapshot.employer_flags):
            continue
        if not _school_ok(rule, snapshot, shift_day):
            continue
        if not _day_ok(rule, snapshot, shift_day):
            continue
        if not _season_ok(rule, shift_day, snapshot.labor_days):
            continue
        if not _trigger_ok(rule, proposal):
            continue
        applicable.append(rule)
    _require_context(applicable, proposal)
    results = []
    for rule in sorted(applicable, key=lambda item: (item.rule_id, item.effective_from)):
        waiver = _waiver(rule, snapshot.waivers, proposal.employee_id, shift_day)
        if waiver and rule.waivable_by_consent:
            results.append(_result(rule, "PASS", None, f"{rule.rule_id} waived by {waiver.waiver_id}", waiver.waiver_id))
            continue
        results.append(_judge_rule(rule, snapshot, proposal, zone, shift_day))
    return Evaluation(
        results=tuple(results),
        binding=tuple(_binding(results)),
        claims_legal_accuracy=bool(results) and all(row.verification_status == "VERIFIED" for row in results),
    )


def load_snapshot(proposal: Proposal, *, dsn: str | None = None) -> Snapshot:
    import psycopg

    url = dsn or os.environ.get("DATABASE_URL") or "postgresql://coffeeshop:coffeeshop@localhost:5432/coffeeshop"
    with psycopg.connect(url) as conn:
        with conn.transaction():
            prepare_session(conn, proposal.tenant_id)
            return _read_snapshot(conn, proposal)


def prepare_session(conn, tenant_id: str) -> None:
    if not tenant_id:
        raise ComplianceUnavailable("tenant_id is required")
    if getattr(conn, "autocommit", False) or getattr(getattr(conn, "info", None), "transaction_status", None) != 2:
        raise ComplianceUnavailable("prepare_session requires an active transaction with autocommit disabled")
    try:
        conn.execute("SET LOCAL ROLE app_rw")
    except Exception as exc:
        raise ComplianceUnavailable(f"SET ROLE app_rw failed: {exc}") from exc
    who = conn.execute("SELECT current_user").fetchone()[0]
    if who != "app_rw":
        raise ComplianceUnavailable(f"refusing to evaluate as {who}; RLS would be bypassed")
    conn.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))


def _read_snapshot(conn, proposal: Proposal) -> Snapshot:
    tenant = conn.execute(
        "SELECT timezone, jurisdiction FROM tenants WHERE id = %s", (proposal.tenant_id,)
    ).fetchone()
    if tenant is None:
        raise ComplianceUnavailable(f"tenant {proposal.tenant_id} not visible")
    employers = conn.execute(
        "SELECT employer_id, fww_covered FROM employer_config"
    ).fetchall()
    if proposal.employer_id:
        employers = [row for row in employers if row[0] == proposal.employer_id]
    if len(employers) != 1:
        raise ComplianceUnavailable(f"expected one employer_config row, found {len(employers)}")
    employee = conn.execute(
        """SELECT employee_id, dob, hourly_rate, is_enrolled, has_other_employer, food_protection_cert
           FROM employees WHERE employee_id = %s""",
        (proposal.employee_id,),
    ).fetchone()
    if employee is None or employee[1] is None:
        raise ComplianceUnavailable(f"employee {proposal.employee_id} not visible or has no dob")
    rules = tuple(_rule_from_row(row) for row in conn.execute(
        """SELECT rule_id, jurisdiction, enforcement, applies_age_min, applies_age_max,
                  applies_school_status, applies_day_type, applies_season, requires_employer_flag,
                  metric, operator, value_num, value_text, unit, consequence, waivable_by_consent,
                  waiver_doc_required, priority, effective_from, effective_to, verification_status
           FROM labor_rules"""
    ).fetchall())
    waivers = tuple(
        Waiver(row[0], row[1], row[2], row[3], row[4], row[5])
        for row in conn.execute(
            """SELECT waiver_id, employee_id, rule_id, valid_from, valid_to, doc_reference
               FROM consent_waivers WHERE employee_id = %s""",
            (proposal.employee_id,),
        ).fetchall()
    )
    days = {}
    if employee[3]:
        cal = conn.execute(
            "SELECT school_calendar_id FROM employees WHERE employee_id = %s", (proposal.employee_id,)
        ).fetchone()[0]
        for row in conn.execute(
            """SELECT date, in_session, day_before_school_day FROM school_calendar_days
               WHERE calendar_id = %s""",
            (cal,),
        ).fetchall():
            days[row[0]] = DayFacts(bool(row[1]), bool(row[2]))
    labor_days = {}
    for (holiday_date,) in conn.execute(
        "SELECT DISTINCT date FROM holidays WHERE holiday = 'Labor Day'"
    ).fetchall():
        if holiday_date.year in labor_days and labor_days[holiday_date.year] != holiday_date:
            raise ComplianceConfigError(f"two Labor Day dates in {holiday_date.year}")
        labor_days[holiday_date.year] = holiday_date
    floors = [rule.value_num for rule in rules if rule.metric == "rate" and rule.operator == ">=" and rule.value_num is not None]
    parts = tuple(part for part in str(tenant[1]).split("+") if part)
    return Snapshot(
        tenant_id=str(proposal.tenant_id),
        timezone=tenant[0],
        jurisdictions=parts,
        employer_flags={"fww_covered": bool(employers[0][1])},
        employee=Employee(employee[0], employee[1], Decimal(employee[2]), bool(employee[3]), employee[4], bool(employee[5])),
        rules=rules,
        waivers=waivers,
        days=days,
        labor_days=labor_days,
        wage_floor=max(floors) if floors else None,
    )


def _rule_from_row(row) -> Rule:
    return Rule(
        rule_id=row[0], jurisdiction=row[1], enforcement=row[2],
        applies_age_min=row[3], applies_age_max=row[4], applies_school_status=row[5],
        applies_day_type=row[6], applies_season=row[7], requires_employer_flag=row[8],
        metric=row[9], operator=row[10], value_num=None if row[11] is None else Decimal(row[11]),
        value_text=row[12], unit=row[13], consequence=row[14],
        waivable_by_consent=bool(row[15]), waiver_doc_required=bool(row[16]),
        priority=row[17] or 0, effective_from=row[18], effective_to=row[19],
        verification_status=row[20] or "",
    )


def _persist(proposal, evaluation, when, *, dsn, log):
    if proposal.shift_id is not None and log is not None and not log.shift_exists(proposal.tenant_id, proposal.shift_id):
        raise ShiftNotPersisted(proposal.shift_id)
    rows = [_log_row(proposal, result, when) for result in evaluation.results]
    if log is not None:
        for row in rows:
            log.insert(row)
        return
    import psycopg
    url = dsn or os.environ.get("DATABASE_URL") or "postgresql://coffeeshop:coffeeshop@localhost:5432/coffeeshop"
    with psycopg.connect(url) as conn:
        with conn.transaction():
            prepare_session(conn, proposal.tenant_id)
            if proposal.shift_id is not None:
                found = conn.execute(
                    "SELECT 1 FROM shifts WHERE shift_id = %s", (proposal.shift_id,)
                ).fetchone()
                if found is None:
                    raise ShiftNotPersisted(proposal.shift_id)
            for row in rows:
                conn.execute(
                    """INSERT INTO rule_eval_log
                       (tenant_id, schedule_id, shift_id, employee_id, rule_id, result,
                        premium_cost_usd, evaluated_at, agent, notes)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (row["tenant_id"], row["schedule_id"], row["shift_id"], row["employee_id"], row["rule_id"],
                     row["result"], row["premium_cost_usd"], row["evaluated_at"], row["agent"], row["notes"]),
                )


def _log_row(proposal, result, when) -> dict:
    note = result.reason if not result.waived_by else f"{result.reason}; waived_by={result.waived_by}"
    return {
        "tenant_id": str(proposal.tenant_id),
        "schedule_id": proposal.schedule_id,
        "shift_id": proposal.shift_id,
        "employee_id": proposal.employee_id,
        "rule_id": result.rule_id,
        "result": result.result,
        "premium_cost_usd": result.premium_cost_usd,
        "evaluated_at": when,
        "agent": AGENT,
        "notes": note,
    }


def _validate_proposal_times(proposal) -> None:
    _validate_span_times(proposal.start_at, proposal.end_at, "shift")
    for index, item in enumerate(proposal.week_shifts or []):
        _validate_span_times(item.start, item.end, f"week_shifts[{index}]")
    for index, item in enumerate(proposal.breaks):
        _validate_span_times(item.start, item.end, f"breaks[{index}]")


def _validate_span_times(start, end, label) -> None:
    if start.tzinfo is None or start.utcoffset() is None:
        raise ComplianceConfigError(f"{label}.start must be timezone-aware")
    if end.tzinfo is None or end.utcoffset() is None:
        raise ComplianceConfigError(f"{label}.end must be timezone-aware")
    if end <= start:
        raise ComplianceConfigError(f"{label}.end must be after {label}.start")


def _judge_rule(rule, snapshot, proposal, zone, shift_day) -> RuleResult:
    key = vocab_key(rule.metric, rule.operator, rule.unit)
    if key == ("role_eligibility", "", "ROLE"):
        raise ComplianceConfigError(
            f"{rule.rule_id} value_text is not a pipe-separated role list ({rule.value_text!r})"
        )
    if key == ("work_window", "BETWEEN", "TIME"):
        return _window(rule, proposal, zone)
    if key == ("hours_paid_min", ">=", "HOURS"):
        scheduled = _hours(proposal.start_at, proposal.end_at)
        paid = min(rule.value_num, scheduled)
        cost = _money(paid * snapshot.employee.hourly_rate)
        return _result(rule, "PREMIUM", cost, f"{rule.rule_id} call-in pay {paid}h x rate", None, rule.value_num)
    if key == ("process", "", "PROCESS"):
        if proposal.requires_employee_to_find_cover:
            return _result(rule, "FAIL", None, f"{rule.rule_id} sick employee cannot be required to find cover", None)
        return _result(rule, "PASS", None, f"{rule.rule_id} cover search stays with the agent", None)
    if key == ("certified_staff_on_site", ">=", "HEADCOUNT"):
        holders = [member for member in proposal.crew or [] if member.food_protection_cert]
        ok = len(holders) >= rule.value_num
        who = ",".join(str(member.employee_id) for member in holders) or "none"
        return _result(
            rule, "PASS" if ok else "FAIL", None,
            f"{rule.rule_id} certified_staff_on_site observed {len(holders)} bound >= {rule.value_num} holders {who}",
            None, rule.value_num,
        )
    if key == ("premium_amount", "", "USD"):
        raise ComplianceConfigError(f"{rule.rule_id} premium tiers are prose; refusing to invent an amount")
    if key == ("rest_hours_between_shifts", ">=", "HOURS"):
        gap = _rest_gap(proposal, zone)
        if gap >= rule.value_num:
            return _result(rule, "PASS", None, f"{rule.rule_id} rest {gap}h >= {rule.value_num}", None, rule.value_num)
        return _result(rule, "FAIL", None, f"{rule.rule_id} clopening rest {gap}h < {rule.value_num} without consent", None, rule.value_num)
    if key == ("advance_notice_days", ">=", "DAYS"):
        if proposal.posted_on is None:
            raise ComplianceConfigError(f"{rule.rule_id} requires posted_on")
        notice = (shift_day - proposal.posted_on).days
        ok = notice >= rule.value_num
        return _result(
            rule, "PASS" if ok else "FAIL", None,
            f"{rule.rule_id} notice {notice} days bound >= {rule.value_num}", None, rule.value_num,
        )
    if key == ("rate", ">=", "USD"):
        ok = snapshot.employee.hourly_rate >= rule.value_num
        return _result(
            rule, "PASS" if ok else "FAIL", None,
            f"{rule.rule_id} rate {snapshot.employee.hourly_rate} bound >= {rule.value_num}", None, rule.value_num,
        )
    if key == ("rate", "", ""):
        return _result(rule, "WARN", None, f"{rule.rule_id} not modeled ({rule.value_text or rule.consequence})", None)
    if key == ("break_minutes", ">=", "MINUTES"):
        return _meal(rule, proposal, zone)
    if key == ("hours_per_day", "<=", "HOURS"):
        observed = _day_hours(proposal, zone, shift_day)
        return _compare(rule, observed, f"{rule.rule_id} hours_per_day {observed}")
    if key == ("hours_per_week", "<=", "HOURS"):
        if rule.value_num is None:
            return _multiemployer(rule, snapshot)
        observed = _week_hours(proposal, zone)
        return _compare(rule, observed, f"{rule.rule_id} hours_per_week {observed}")
    if key == ("days_per_week", "<=", "DAYS"):
        observed = Decimal(len(_week_dates(proposal, zone)))
        return _compare(rule, observed, f"{rule.rule_id} days_per_week {observed}")
    if key == ("hours_per_week", "TRIGGER_WHEN_GT", "MULTIPLIER"):
        observed = _week_hours(proposal, zone)
        if observed <= rule.value_num:
            return _result(rule, "PASS", None, f"{rule.rule_id} hours_per_week {observed} <= {rule.value_num}", None, rule.value_num)
        if not rule.consequence or "1.5x" not in rule.consequence.replace(" ", ""):
            raise ComplianceConfigError(f"{rule.rule_id} overtime multiplier is not a structured 1.5x")
        # shortcut: the $25.50 OT floor stays prose until it is its own column.
        extra = _money((observed - rule.value_num) * snapshot.employee.hourly_rate * Decimal("0.5"))
        return _result(rule, "PREMIUM", extra, f"{rule.rule_id} overtime {observed - rule.value_num}h at 0.5x extra", None, rule.value_num)
    if key == ("span_hours", "TRIGGER_WHEN_GT", "HOURS"):
        observed = _span_hours(proposal, zone, shift_day)
        if observed <= rule.value_num:
            return _result(rule, "PASS", None, f"{rule.rule_id} span {observed}h <= {rule.value_num}", None, rule.value_num)
        if snapshot.wage_floor is None:
            raise ComplianceConfigError(f"{rule.rule_id} spread premium needs a rate >= wage-floor rule")
        return _result(rule, "PREMIUM", _money(snapshot.wage_floor), f"{rule.rule_id} spread {observed}h adds 1h at minimum wage", None, rule.value_num)
    raise ComplianceConfigError(f"no handler for {key}")


def _meal(rule, proposal, zone) -> RuleResult:
    start = proposal.start_at.astimezone(zone)
    end = proposal.end_at.astimezone(zone)
    text = _norm(rule.value_text)
    consequence = _norm(rule.consequence)
    length = _hours(proposal.start_at, proposal.end_at)
    if text.startswith("start "):
        window = _clock_span(text)
        triggered = (length > 6 if ">6" in consequence else True) and _in_wrap(start.time(), window)
        if not triggered:
            return _result(rule, "PASS", None, f"{rule.rule_id} meal condition not met", None, rule.value_num)
        ok = _midway_break(proposal, zone, rule.value_num) if "midway" in consequence else _break_minutes(proposal) >= rule.value_num
        return _result(rule, "PASS" if ok else "FAIL", None, f"{rule.rule_id} break_minutes bound >= {rule.value_num}", None, rule.value_num)
    window = _clock_span(text)
    before = re.search(r"before (\d{1,2}:\d{2}) to after (\d{1,2}:\d{2})", consequence)
    if before:
        day_start, day_end = _day_bounds(proposal, zone, start.date())
        triggered = day_start.time() < _clock(before.group(1)) and day_end.time() > _clock(before.group(2))
        if day_end.date() > start.date():
            triggered = day_start.time() < _clock(before.group(1))
    else:
        triggered = _spans(start, end, window) and (length > 6 if ">6" in consequence else True)
    if not triggered:
        return _result(rule, "PASS", None, f"{rule.rule_id} meal condition not met", None, rule.value_num)
    inside = _break_minutes_inside(proposal, zone, window)
    ok = inside >= rule.value_num
    return _result(rule, "PASS" if ok else "FAIL", None, f"{rule.rule_id} break inside window {inside} bound >= {rule.value_num}", None, rule.value_num)


def _window(rule, proposal, zone) -> RuleResult:
    start = proposal.start_at.astimezone(zone)
    end = proposal.end_at.astimezone(zone)
    open_m, close_m = _clock_span(rule.value_text or "")
    start_m = _minutes(start.time())
    end_m = _minutes(end.time()) + (1440 if end.date() > start.date() else 0)
    ok = start_m >= open_m and end_m <= close_m
    return _result(
        rule, "PASS" if ok else "FAIL", None,
        f"{rule.rule_id} work_window {start.strftime('%H:%M')}-{end.strftime('%H:%M')} bound {rule.value_text}",
        None,
    )


def _multiemployer(rule, snapshot) -> RuleResult:
    flag = snapshot.employee.has_other_employer
    if flag is None:
        raise ComplianceConfigError(f"{rule.rule_id} requires has_other_employer")
    if flag:
        return _result(rule, "FAIL", None, f"{rule.rule_id} outside hours are unattested; combined cap cannot be checked", None)
    return _result(rule, "PASS", None, f"{rule.rule_id} no outside employer", None)


def _compare(rule, observed, label) -> RuleResult:
    ok = _op(rule.operator, observed, rule.value_num)
    return _result(
        rule, "PASS" if ok else "FAIL", None,
        f"{label} bound {rule.operator} {rule.value_num}", None, rule.value_num,
    )


def _binding(results: list[RuleResult]) -> list[RuleResult]:
    hard = [row for row in results if row.result == "FAIL" and row.enforcement == "HARD"]
    chosen = {}
    for row in hard:
        current = chosen.get(row.metric)
        if current is None or _stricter(row, current):
            chosen[row.metric] = row
    premiums = [row for row in results if row.result == "PREMIUM"]
    process = [row for row in results if row.result == "FAIL" and row.enforcement == "PROCESS"]
    return list(chosen.values()) + premiums + process


def _stricter(row, other) -> bool:
    if row.bound is None or other.bound is None or row.bound == other.bound:
        return row.priority > other.priority
    # A maximum is stricter when smaller; a minimum is stricter when larger.
    return row.bound < other.bound if _is_max(row) else row.bound > other.bound


def _is_max(row) -> bool:
    return row.metric in {"hours_per_day", "hours_per_week", "days_per_week", "span_hours"}


def _result(rule, result, cost, reason, waived, bound=None) -> RuleResult:
    if result not in RESULTS:
        raise ComplianceConfigError(result)
    return RuleResult(
        rule.rule_id, result, cost, reason, rule.verification_status, waived,
        rule.metric, rule.priority, rule.enforcement, bound,
    )


def _require_context(rules, proposal) -> None:
    metrics = {rule.metric for rule in rules}
    if metrics & WEEKLY_METRICS and (proposal.week_shifts is None or not proposal.week_complete):
        raise ComplianceConfigError("week_shifts and week_complete=true are required before weekly or rest rules can pass")
    if "certified_staff_on_site" in metrics and (proposal.crew is None or not proposal.crew_complete):
        raise ComplianceConfigError("crew and crew_complete=true are required before an on-site rule can pass")


def _trigger_ok(rule, proposal) -> bool:
    key = vocab_key(rule.metric, rule.operator, rule.unit)
    if key == ("hours_paid_min", ">=", "HOURS"):
        return proposal.sent_home
    if key == ("process", "", "PROCESS"):
        return proposal.call_out_reason == "sick"
    if key == ("premium_amount", "", "USD"):
        return proposal.schedule_change
    return True


def _in_force(rule, day) -> bool:
    if day < rule.effective_from:
        return False
    return rule.effective_to is None or day < rule.effective_to


def _jurisdiction_ok(rule, jurisdictions) -> bool:
    if not rule.jurisdiction:
        raise ComplianceConfigError("rule jurisdiction is null")
    return rule.jurisdiction == FEDERAL or rule.jurisdiction in jurisdictions


def _age_ok(rule, age) -> bool:
    if rule.applies_age_min is not None and age < rule.applies_age_min:
        return False
    if rule.applies_age_max is not None and age > rule.applies_age_max:
        return False
    return True


def _flag_ok(rule, flags) -> bool:
    spec = (rule.requires_employer_flag or "").strip()
    if not spec:
        return True
    match = FLAG.fullmatch(spec)
    if not match:
        raise ComplianceConfigError(f"{rule.rule_id} has unreadable employer flag {spec!r}")
    name, want = match.group(1), match.group(2).upper() == "TRUE"
    if name not in flags or flags[name] is None:
        raise ComplianceConfigError(f"{rule.rule_id} flag {name} is missing")
    return flags[name] is want


def _school_ok(rule, snapshot, day) -> bool:
    status = (rule.applies_school_status or "ANY").upper()
    if status == "ANY":
        return True
    return status == _school_status(snapshot, day)


def _day_ok(rule, snapshot, day) -> bool:
    kind = (rule.applies_day_type or "ANY").upper()
    if kind == "ANY":
        return True
    if not snapshot.employee.is_enrolled:
        return kind == "NON_SCHOOL_DAY"
    facts = snapshot.days[day]
    if kind == "SCHOOL_DAY":
        return facts.in_session
    if kind == "NON_SCHOOL_DAY":
        return not facts.in_session
    if kind == "DAY_BEFORE_SCHOOL_DAY":
        return facts.day_before_school_day
    raise ComplianceConfigError(f"{rule.rule_id} has unknown day type {kind}")


def _school_status(snapshot, day) -> str:
    if not snapshot.employee.is_enrolled:
        return "NOT_ENROLLED"
    return "IN_SESSION" if snapshot.days[day].in_session else "NOT_IN_SESSION"


def _season_ok(rule, day, labor_days) -> bool:
    season = (rule.applies_season or "").strip()
    if not season:
        return True
    labor = labor_days.get(day.year)
    if labor is None:
        raise ComplianceConfigError(f"no Labor Day for {day.year}")
    text = _norm(season)
    if text == "jun 1 to labor day":
        return date(day.year, 6, 1) <= day <= labor
    if text == "jun 21 to labor day":
        return date(day.year, 6, 21) <= day <= labor
    if text == "labor day+1 to jun 20":
        return day >= labor + timedelta(days=1) or day <= date(day.year, 6, 20)
    raise ComplianceConfigError(f"{rule.rule_id} has unknown season {season!r}")


def _waiver(rule, waivers, employee_id, day):
    found = None
    for waiver in waivers:
        if waiver.employee_id != employee_id or waiver.rule_id != rule.rule_id:
            continue
        if day < waiver.valid_from or (waiver.valid_to is not None and day >= waiver.valid_to):
            continue
        if rule.waiver_doc_required and not (waiver.doc_reference or "").strip():
            continue
        found = waiver
    return found


def _op(operator, observed, bound) -> bool:
    if operator == "<=":
        return observed <= bound
    if operator == ">=":
        return observed >= bound
    if operator == "<":
        return observed < bound
    if operator == ">":
        return observed > bound
    if operator == "==":
        return observed == bound
    if operator == "!=":
        return observed != bound
    raise ComplianceConfigError(f"unknown operator {operator}")


def _age(dob, day) -> int:
    return day.year - dob.year - ((day.month, day.day) < (dob.month, dob.day))


def _hours(start, end) -> Decimal:
    return (Decimal((end - start).total_seconds()) / Decimal(3600)).quantize(Decimal("0.01"))


def _money(amount) -> Decimal:
    return Decimal(amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _week_start(proposal, zone) -> date:
    if proposal.week_start:
        return proposal.week_start
    day = proposal.start_at.astimezone(zone).date()
    return day - timedelta(days=day.weekday())


def _shifts(proposal) -> list[ShiftSpan]:
    own = ShiftSpan(proposal.start_at, proposal.end_at, proposal.shift_id)
    others = []
    for span in proposal.week_shifts or []:
        if span.start == own.start and span.end == own.end:
            continue
        others.append(span)
    return [own, *others]


def _in_week(span, proposal, zone) -> bool:
    start = _week_start(proposal, zone)
    day = span.start.astimezone(zone).date()
    if not start <= day < start + timedelta(days=7):
        raise ComplianceConfigError(f"shift on {day} is outside week {start}")
    return True


def _week_hours(proposal, zone) -> Decimal:
    total = Decimal(0)
    for span in _shifts(proposal):
        _in_week(span, proposal, zone)
        total += _hours(span.start, span.end)
    total -= _break_hours(proposal)
    return max(total, Decimal(0))


def _day_hours(proposal, zone, day) -> Decimal:
    total = Decimal(0)
    for span in _shifts(proposal):
        if span.start.astimezone(zone).date() == day:
            total += _hours(span.start, span.end)
    if proposal.start_at.astimezone(zone).date() == day:
        total -= _break_hours(proposal)
    return max(total, Decimal(0))


def _week_dates(proposal, zone) -> set[date]:
    dates = set()
    for span in _shifts(proposal):
        _in_week(span, proposal, zone)
        dates.add(span.start.astimezone(zone).date())
    return dates


def _span_hours(proposal, zone, day) -> Decimal:
    overlapping = []
    for span in _shifts(proposal):
        start = span.start.astimezone(zone)
        end = span.end.astimezone(zone)
        if start.date() == day or end.date() == day or start.date() < day < end.date():
            overlapping.append((start, end))
    if not overlapping:
        return Decimal(0)
    earliest = min(item[0] for item in overlapping)
    latest = max(item[1] for item in overlapping)
    return _hours(earliest, latest)


def _rest_gap(proposal, zone) -> Decimal:
    start = proposal.start_at.astimezone(zone)
    prior = [
        span.end.astimezone(zone)
        for span in (proposal.week_shifts or [])
        if span.end.astimezone(zone) <= start
    ]
    if not prior:
        return Decimal(24)
    return _hours(max(prior), start)


def _break_hours(proposal) -> Decimal:
    return sum((_hours(item.start, item.end) for item in proposal.breaks), Decimal(0))


def _break_minutes(proposal) -> Decimal:
    return _break_hours(proposal) * 60


def _midway_break(proposal, zone, required) -> bool:
    start = proposal.start_at.astimezone(zone)
    end = proposal.end_at.astimezone(zone)
    mid = start + (end - start) / 2
    return any(
        item.start.astimezone(zone) <= mid <= item.end.astimezone(zone)
        and _hours(item.start, item.end) * 60 >= required
        for item in proposal.breaks
    )


def _break_minutes_inside(proposal, zone, window) -> Decimal:
    total = Decimal(0)
    for item in proposal.breaks:
        start = item.start.astimezone(zone)
        end = item.end.astimezone(zone)
        begin = max(_minutes(start.time()), window[0])
        finish = min(_minutes(end.time()) + (1440 if end.date() > start.date() else 0), window[1])
        if finish > begin:
            total += (Decimal(finish - begin) / Decimal(60))
    return total


def _day_bounds(proposal, zone, day):
    spans = [span for span in _shifts(proposal) if span.start.astimezone(zone).date() == day or span.end.astimezone(zone).date() == day]
    start = min(span.start.astimezone(zone) for span in spans)
    end = max(span.end.astimezone(zone) for span in spans)
    return start, end


def _spans(start, end, window) -> bool:
    return _minutes(start.time()) < window[1] and (_minutes(end.time()) + (1440 if end.date() > start.date() else 0)) > window[0]


def _in_wrap(value, window) -> bool:
    minute = _minutes(value)
    if window[0] <= window[1]:
        return window[0] <= minute <= window[1]
    return minute >= window[0] or minute <= window[1]


def _clock_span(text) -> tuple[int, int]:
    clocks = CLOCK.findall(text.replace("24:00", "24:00"))
    if len(clocks) < 2:
        raise ComplianceConfigError(f"unreadable time window {text!r}")
    return _minutes(_clock(clocks[0]), allow_24=True), _minutes(_clock(clocks[1]), allow_24=True)


def _clock(text) -> time:
    if text == "24:00":
        return time(0, 0)
    hour, minute = text.split(":")
    return time(int(hour), int(minute))


def _minutes(value, allow_24=False) -> int:
    if allow_24 and value == time(0, 0):
        return 24 * 60
    return value.hour * 60 + value.minute


def _norm(text) -> str:
    return (text or "").replace("–", "-").replace("—", "-").lower()
