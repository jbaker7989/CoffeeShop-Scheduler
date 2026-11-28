"""Write one representative published week for the Maven demo tenant."""
from __future__ import annotations

import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.scheduler.persist import persist_schedule
from agents.scheduler.runner import ScheduledShift, ScheduleEconomics, ScheduleOutput
from agents.scheduler.solve import SolveResult, SolverStopPolicy
from data_access.session import tenant_session

TENANT_ID = "00000000-0000-0000-0000-000000000001"
WEEK_START = date(2026, 11, 23)
MODEL_VERSION = "maven-demo-v1"


def seed() -> None:
    with tenant_session(role="app_rw", tenant_id=TENANT_ID) as conn:
        if conn.execute("SELECT slug FROM tenants").fetchone() != ("maven-demo",):
            raise ValueError("expected the seeded Maven demo tenant")
        location_id = conn.execute("SELECT min(location_id) FROM stores").fetchone()[0]
        employees = conn.execute(
            """
            SELECT DISTINCT ON (e.role) e.employee_id, e.role, e.hourly_rate,
                (SELECT min(station_id) FROM stations WHERE location_id = %s AND staffed_by_role = e.role)
            FROM employees AS e
            WHERE e.home_location_id = %s AND e.is_enrolled IS FALSE AND e.dob <= %s
              AND e.hourly_rate IS NOT NULL AND e.role IS NOT NULL
            ORDER BY e.role, e.food_protection_cert DESC NULLS LAST, e.employee_id
            """,
            (location_id, location_id, WEEK_START.replace(year=WEEK_START.year - 18)),
        ).fetchall()
        if not employees:
            raise ValueError("load the Maven demo roster before seeding a schedule")

        shifts = []
        for offset, start in enumerate((8, 9, 12, 14, 17, 19, 10)):
            for employee_id, role, rate, station_id in employees:
                shifts.append(ScheduledShift(
                    employee_id=employee_id, role=role, location_id=location_id,
                    day=WEEK_START + timedelta(days=offset), start_hour=Decimal(start),
                    end_hour=Decimal(start + 3), hours=Decimal(3), hourly_rate=rate,
                    labour_cost_cents=int(rate * 300), premium_cents=0, premium_rule_ids=(),
                    station_id=station_id, is_meal_break_covered=None,
                ))
        labour_cents = sum(shift.labour_cost_cents for shift in shifts)
        output = ScheduleOutput(
            target_window=(WEEK_START, WEEK_START + timedelta(days=6)),
            model_version=MODEL_VERSION,
            shifts=tuple(shifts),
            economics=ScheduleEconomics(labour_cents, 0, 6 * len(shifts), len(employees), {location_id: labour_cents}),
            solve=SolveResult((), 0, "NOT_RUN", 0, 0, "limit_or_unknown", 0, SolverStopPolicy(1, 0)),
            objective=0,
            omitted_terms=(),
        )
        schedule_id = persist_schedule(
            conn, output, tenant_id=TENANT_ID, location_id=location_id, week_start=WEEK_START,
            status="published", solver_version=MODEL_VERSION,
        )
    print(f"Published maven-demo schedule {schedule_id} for store {location_id}, week of {WEEK_START}.")


if __name__ == "__main__":
    seed()
