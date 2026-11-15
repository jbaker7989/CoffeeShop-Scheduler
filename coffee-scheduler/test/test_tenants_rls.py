"""Regression coverage for BUG-020: tenants root-table RLS isolation."""

import os
import unittest
import uuid

import psycopg


DATABASE_URL = os.environ.get("DATABASE_URL")
MAVEN_TENANT_ID = "00000000-0000-0000-0000-000000000001"
MAVEN_SLUG = "maven-demo"
PROBE_SLUG = "bug020-probe"


@unittest.skipUnless(DATABASE_URL, "DATABASE_URL is not set")
class TenantsRlsTests(unittest.TestCase):
    """Exercise the policy as non-owner app roles and as the ETL/owner role."""

    def setUp(self):
        self.connection = psycopg.connect(DATABASE_URL)
        self.probe_id = str(uuid.uuid4())
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO tenants (id, slug, name, status, plan, jurisdiction)
                VALUES (%s, %s, %s, 'active', 'starter', 'NY-STATE+NYC')
                """,
                (self.probe_id, PROBE_SLUG, "BUG-020 RLS probe"),
            )
        self.connection.commit()

    def tearDown(self):
        if not hasattr(self, "connection"):
            return
        try:
            with self.connection.cursor() as cursor:
                cursor.execute("DELETE FROM tenants WHERE id = %s", (self.probe_id,))
            self.connection.commit()
        finally:
            self.connection.close()

    def _slugs_as_role(self, role, tenant_id=None):
        with self.connection.transaction():
            with self.connection.cursor() as cursor:
                cursor.execute(f"SET LOCAL ROLE {role}")
                if tenant_id is not None:
                    cursor.execute(
                        "SELECT set_config('app.tenant_id', %s, true)", (tenant_id,)
                    )
                cursor.execute("SELECT slug FROM tenants ORDER BY slug")
                return {row[0] for row in cursor.fetchall()}

    # spec: F60.ac4 — BUG-020 app roles may see only their own tenant root row.
    def test_app_roles_cannot_enumerate_other_tenants(self):
        for role in ("app_rw", "app_ro"):
            with self.subTest(role=role):
                self.assertEqual(
                    self._slugs_as_role(role, MAVEN_TENANT_ID),
                    {MAVEN_SLUG},
                )

    # spec: F60.ac4 — BUG-020 an unset tenant context fails closed.
    def test_app_rw_without_tenant_context_sees_zero_tenants(self):
        self.assertEqual(self._slugs_as_role("app_rw"), set())

    # spec: F60.ac4 — BUG-020 ENABLE, not FORCE, preserves the owner/ETL path.
    def test_owner_still_sees_maven_and_probe_tenants(self):
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT slug FROM tenants WHERE slug IN (%s, %s)",
                (MAVEN_SLUG, PROBE_SLUG),
            )
            self.assertEqual({row[0] for row in cursor.fetchall()}, {MAVEN_SLUG, PROBE_SLUG})

    # spec: F60.ac4 — BUG-020 the catalog records enabled self-scoping RLS.
    def test_tenants_rls_catalog_has_self_scoping_policy(self):
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT c.relrowsecurity, c.relforcerowsecurity, p.polqual::text
                FROM pg_class AS c
                JOIN pg_namespace AS n ON n.oid = c.relnamespace
                JOIN pg_policy AS p ON p.polrelid = c.oid
                WHERE n.nspname = 'public'
                  AND c.relname = 'tenants'
                  AND pg_get_expr(p.polqual, p.polrelid) LIKE '%app.tenant_id%'
                """
            )
            rows = cursor.fetchall()
        self.assertTrue(rows, "tenants needs a policy scoped by app.tenant_id")
        self.assertTrue(all(row[0] for row in rows), "tenants must have RLS enabled")
        self.assertTrue(
            all(not row[1] for row in rows),
            "tenants deliberately uses ENABLE RLS without FORCE for owner/ETL access",
        )


if __name__ == "__main__":
    unittest.main()
