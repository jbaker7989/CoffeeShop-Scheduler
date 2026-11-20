"""Populate labor_rule_templates from the verified maven-demo NY rule set (BUG-058).

Migration 0016 snapshots the template at migration time, but on a clean bootstrap
``alembic upgrade head`` runs BEFORE the ETL loads maven-demo's labor_rules, so the
migration-time snapshot is empty and onboarding provisioning has no verified rule set.
This idempotent step runs AFTER the ETL (the documented bootstrap order:
migrate -> ETL -> seed_rule_templates) and backfills the template; ``ON CONFLICT DO
NOTHING`` makes re-runs a no-op. Connect as the owner/migrator role (BYPASSRLS) to read
the FORCE-RLS labor_rules.

Run: python3 scripts/seed_rule_templates.py --database-url "$DATABASE_URL"
"""
from __future__ import annotations

import argparse
import sys

import psycopg

_SOURCE_JURISDICTIONS = ("US-FED", "NY-STATE", "NYC")
_TARGET_JURISDICTION = "NY-STATE+NYC"

_SEED_SQL = """
INSERT INTO labor_rule_templates (
 jurisdiction, rule_id, rule_category, rule_jurisdiction, wage_order, enforcement,
 applies_age_min, applies_age_max, applies_school_status, applies_day_type,
 applies_season, requires_employer_flag, metric, operator, value_num, value_time,
 value_text, unit, consequence, waivable_by_consent, waiver_doc_required, priority,
 effective_from, effective_to, source_citation, source_url, verification_status,
 verified_on, notes
)
SELECT %s, r.rule_id, r.rule_category, r.jurisdiction, r.wage_order, r.enforcement,
 r.applies_age_min, r.applies_age_max, r.applies_school_status, r.applies_day_type,
 r.applies_season, r.requires_employer_flag, r.metric, r.operator, r.value_num,
 r.value_time, r.value_text, r.unit, r.consequence, r.waivable_by_consent,
 r.waiver_doc_required, r.priority, r.effective_from, r.effective_to, r.source_citation,
 r.source_url, r.verification_status, r.verified_on, r.notes
FROM labor_rules r JOIN tenants t ON t.id = r.tenant_id
WHERE t.slug = 'maven-demo' AND r.jurisdiction = ANY(%s) AND r.deleted_at IS NULL
ON CONFLICT (jurisdiction, rule_id, effective_from) DO NOTHING
"""


def seed(database_url: str) -> int:
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(_SEED_SQL, (_TARGET_JURISDICTION, list(_SOURCE_JURISDICTIONS)))
            inserted = cur.rowcount
            total = cur.execute(
                "SELECT count(*) FROM labor_rule_templates WHERE jurisdiction = %s",
                (_TARGET_JURISDICTION,),
            ).fetchone()[0]
        conn.commit()
    print(f"seed_rule_templates: inserted {inserted}, {_TARGET_JURISDICTION} template now has {total} rules")
    return total


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backfill labor_rule_templates from the verified NY rule set")
    parser.add_argument("--database-url", required=True)
    args = parser.parse_args(argv)
    total = seed(args.database_url)
    if total == 0:
        print("WARNING: no NY rules found to template — did the ETL run first?", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
