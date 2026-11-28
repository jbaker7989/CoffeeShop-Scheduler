"""CS-022 PostgreSQL contract tests. Set DATABASE_URL for a migrated, seeded demo DB."""
import os
import sys
import unittest
from datetime import date
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from compliance.coverage_law import DateRange, reconcile
from compliance.coverage_law_gate import GateSetupError, load_tenant_rules

DSN = os.environ.get("DATABASE_URL")
MAVEN = "00000000-0000-0000-0000-000000000001"


@unittest.skipUnless(DSN, "DATABASE_URL is not set (CS-022 database suite)")
class CoverageLawDatabaseTests(unittest.TestCase):
    def setUp(self):
        import psycopg

        from data_access.session import open_session_context

        self.scope = open_session_context
        self.conn = psycopg.connect(DSN, connect_timeout=5)
        self.addCleanup(self.conn.close)
        self.addCleanup(self.conn.rollback)
        self.conn.execute("BEGIN")

    def create_tenant(self):
        # Bootstrap only the tenant root. All fixture rule writes and reads use app roles.
        tenant = str(uuid4())
        slug = f"cs022-{tenant}"
        self.conn.execute(
            "INSERT INTO tenants (id, slug, name, status) VALUES (%s, %s, 'CS-022 synthetic tenant', 'active')",
            (tenant, slug),
        )
        return tenant, slug

    def add_labor_rule(self, tenant, rule_id, *, deleted=False):
        self.conn.execute(
            """INSERT INTO labor_rules
                   (tenant_id, rule_id, effective_from, enforcement, verification_status,
                    verified_on, source_citation, source_url, deleted_at)
               VALUES (%s, %s, '2030-01-01', 'HARD', 'VERIFIED', '2030-01-01',
                       'Synthetic citation', 'https://example.invalid/cs022',
                       CASE WHEN %s THEN now() END)""",
            (tenant, rule_id, deleted),
        )

    def add_coverage_rule(self, tenant, rule_id, description, validity="[2030-01-01,2030-02-01)", *, deleted=False):
        self.conn.execute(
            """INSERT INTO coverage_rules (tenant_id, rule_id, description, validity, deleted_at)
               VALUES (%s, %s, %s, %s::daterange, CASE WHEN %s THEN now() END)""",
            (tenant, rule_id, description, validity, deleted),
        )

    # spec: F26.ac6 — canonical fixture identity, row counts, and range decoding are verified under app-role RLS.
    def test_canonical_seed_identity_and_counts(self):
        self.scope(self.conn, role="app_ro", tenant_id=MAVEN)
        coverage, labor = load_tenant_rules(self.conn, MAVEN, "maven-demo")
        self.assertGreaterEqual(len(coverage), 8)
        self.assertGreaterEqual(len(labor), 31)
        self.assertEqual({row.tenant_id for row in (*coverage, *labor)}, {MAVEN})
        self.assertTrue(any(row.rule_id == "COV-FPC" for row in coverage))
        self.assertTrue(any(row.rule_id == "NYC-FPC-COVERAGE" for row in labor))
        with self.assertRaisesRegex(GateSetupError, "tenant identity"):
            load_tenant_rules(self.conn, MAVEN, "wrong-slug")

    # spec: F26.ac2 — foreign and deleted exact IDs cannot resolve a citation.
    # spec: F26.ac6 — tenant and soft-delete scoping applies to both tables, even with visible decoys.
    def test_scoped_live_rows(self):
        tenant, slug = self.create_tenant()
        other, other_slug = self.create_tenant()
        self.scope(self.conn, role="app_rw", tenant_id=other)
        self.add_labor_rule(other, "TEST-FOREIGN")
        self.add_coverage_rule(other, "COV-FOREIGN", "LAW TEST-FOREIGN")
        self.scope(self.conn, role="app_ro", tenant_id=other)
        foreign_coverage, foreign_labor = load_tenant_rules(self.conn, other, other_slug)
        self.assertEqual(reconcile(foreign_coverage, foreign_labor), [])

        self.scope(self.conn, role="app_rw", tenant_id=tenant)
        self.add_labor_rule(tenant, "TEST-FOREIGN-EXT")
        self.add_labor_rule(tenant, "TEST-DELETED", deleted=True)
        self.add_coverage_rule(tenant, "COV-LIVE", "LAW TEST-FOREIGN TEST-DELETED", "(2030-01-01,2030-02-01]")
        self.add_coverage_rule(tenant, "COV-DELETED", "LAW TEST-MISSING", deleted=True)
        self.add_coverage_rule(tenant, "COV-NULL", "LAW TEST-FOREIGN-EXT", None)
        self.add_coverage_rule(tenant, "COV-EMPTY", "LAW TEST-FOREIGN-EXT", "empty")
        self.scope(self.conn, role="app_ro", tenant_id=tenant)
        # Exercise the RLS policies independently of the loader's explicit WHERE clauses.
        for query, ids in (
            ("SELECT rule_id FROM coverage_rules", {"COV-LIVE", "COV-NULL", "COV-EMPTY"}),
            ("SELECT rule_id FROM labor_rules", {"TEST-FOREIGN-EXT"}),
        ):
            self.assertEqual({row[0] for row in self.conn.execute(query)}, ids)
        coverage, labor = load_tenant_rules(self.conn, tenant, slug)
        self.assertEqual({row.rule_id: row.validity for row in coverage}, {
            "COV-LIVE": DateRange(date(2030, 1, 2), date(2030, 2, 2)),
            "COV-NULL": None,
            "COV-EMPTY": DateRange(empty=True),
        })
        self.assertEqual([row.rule_id for row in labor], ["TEST-FOREIGN-EXT"])
        self.assertEqual(
            [(f.coverage_rule_id, f.labor_rule_id, f.condition) for f in reconcile(coverage, labor)],
            [("COV-EMPTY", None, "invalid_coverage_validity"), ("COV-LIVE", "TEST-DELETED", "missing_rule"),
             ("COV-LIVE", "TEST-FOREIGN", "missing_rule"), ("COV-NULL", None, "invalid_coverage_validity")],
        )

    # spec: F26.ac6 — missing or incorrectly scoped seed rows cannot pass the database gate.
    def test_missing_seed_fails_closed(self):
        tenant, slug = self.create_tenant()
        self.scope(self.conn, role="app_ro", tenant_id=tenant)
        with self.assertRaisesRegex(GateSetupError, "coverage=0 labor=0"):
            load_tenant_rules(self.conn, tenant, slug)
        with self.assertRaisesRegex(GateSetupError, "tenant context"):
            load_tenant_rules(self.conn, MAVEN, "maven-demo")
        self.scope(self.conn, role="app_rw", tenant_id=tenant)
        self.add_coverage_rule(tenant, "COV-CREW", "Business assumption", None)
        self.scope(self.conn, role="app_ro", tenant_id=tenant)
        with self.assertRaisesRegex(GateSetupError, "coverage=1 labor=0"):
            load_tenant_rules(self.conn, tenant, slug)


if __name__ == "__main__":
    unittest.main()
