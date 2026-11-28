"""CS-045 / F45 integration tests for the public data-access API.

All mutations deliberately raise ``Rollback`` before their transaction ends, so
the maven-demo fixtures remain the shared read-only baseline.
"""
import os
import sys
import unittest
import uuid
from pathlib import Path

SCHEDULER_ROOT = Path(__file__).resolve().parents[1]
if str(SCHEDULER_ROOT) not in sys.path:
    sys.path.insert(0, str(SCHEDULER_ROOT))

DSN = os.environ.get("DATABASE_URL")
TENANT = "00000000-0000-0000-0000-000000000001"

try:
    import psycopg
    from psycopg import Rollback
    from psycopg.errors import InsufficientPrivilege

    from data_access import (
        AppendOnlyError,
        CheckpointRepo,
        Repository,
        introspect,
        open_session_context,
        tenant_session,
    )
except ImportError:  # pragma: no cover - permits clean skip in minimal environments
    psycopg = None


@unittest.skipUnless(DSN and psycopg, "DATABASE_URL and psycopg are required")
class DataAccessSpecTests(unittest.TestCase):
    def _employee_values(self, employee_id, first_name="CS045"):
        return {
            "tenant_id": TENANT,
            "employee_id": employee_id,
            "first_name": first_name,
            "last_name": "Rollback",
        }

    # spec: F45.ac1 — real composite keys, global table carve-out, and restricted PII.
    def test_catalog_metadata_and_composite_key_get(self):
        with tenant_session(DSN, role="app_ro", tenant_id=TENANT) as conn:
            employees = Repository(conn, "employees")
            self.assertEqual(("tenant_id", "employee_id"), employees.spec.pk)
            employee = employees.list(limit=1, order_by="employee_id")[0]
            found = employees.get(
                tenant_id=employee["tenant_id"], employee_id=employee["employee_id"]
            )
            self.assertEqual(employee["employee_id"], found["employee_id"])

            weather_codes = Repository(conn, "weather_codes").spec
            self.assertFalse(weather_codes.tenant_scoped)
            self.assertNotIn("tenant_id", weather_codes.columns)
            with self.assertRaises(PermissionError):
                introspect(conn, "employee_pii")

    # spec: F45.ac2 — hostile values stay values; repository parameters never execute them.
    def test_insert_parameterizes_hostile_values_and_rolls_back(self):
        hostile = "x'; DROP TABLE employees;--"
        employee_id = 940045
        with tenant_session(DSN, role="app_rw", tenant_id=TENANT) as conn:
            employees = Repository(conn, "employees")
            before = conn.execute("SELECT count(*) FROM employees").fetchone()[0]
            inserted = employees.insert(**self._employee_values(employee_id, hostile))
            self.assertEqual(hostile, inserted["first_name"])
            self.assertEqual(hostile, employees.get(
                tenant_id=TENANT, employee_id=employee_id
            )["first_name"])
            self.assertEqual(before + 1, conn.execute("SELECT count(*) FROM employees").fetchone()[0])
            raise Rollback

        with tenant_session(DSN, role="app_rw", tenant_id=TENANT) as conn:
            self.assertEqual(
                before, conn.execute("SELECT count(*) FROM employees").fetchone()[0]
            )

    # spec: F45.ac3 — RLS scopes tenant rows and fails closed without a tenant GUC.
    def test_rls_scopes_rows_and_fails_closed(self):
        with tenant_session(DSN, role="app_rw", tenant_id=TENANT) as conn:
            self.assertGreater(len(Repository(conn, "employees").list(limit=1)), 0)

        with tenant_session(DSN, role="app_rw", tenant_id=str(uuid.uuid4())) as conn:
            self.assertEqual([], Repository(conn, "employees").list(limit=1))

        with psycopg.connect(DSN) as conn, conn.transaction():
            conn.execute("SET LOCAL ROLE app_rw")
            self.assertEqual(0, conn.execute("SELECT count(*) FROM employees").fetchone()[0])

    # spec: F45.ac4 — checkpoint latest uses monotonic sequence and the table is FORCE RLS-protected.
    def test_checkpoints_are_force_rls_and_latest_is_second_save(self):
        agent_id = uuid.uuid4()
        with tenant_session(DSN, role="app_rw", tenant_id=TENANT) as conn:
            checkpoints = CheckpointRepo(conn)
            first = checkpoints.save(
                tenant_id=TENANT, agent_id=agent_id, checkpoint={"ordinal": 1}
            )
            second = checkpoints.save(
                tenant_id=TENANT, agent_id=agent_id, checkpoint={"ordinal": 2}
            )
            latest = checkpoints.get_latest(tenant_id=TENANT, agent_id=agent_id)
            self.assertGreater(second["seq"], first["seq"])
            self.assertEqual(second["id"], latest["id"])
            self.assertEqual({"ordinal": 2}, latest["checkpoint"])

            forced = conn.execute(
                "SELECT c.relforcerowsecurity FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relname = 'langgraph_checkpoints'"
            ).fetchone()[0]
            policy = conn.execute(
                "SELECT 1 FROM pg_policies WHERE schemaname = 'public' "
                "AND tablename = 'langgraph_checkpoints' AND policyname = 'tenant_isolation'"
            ).fetchone()
            self.assertTrue(forced)
            self.assertIsNotNone(policy)
            raise Rollback

    # spec: F45.ac5 — append-only repositories reject mutation and retain insert/read access.
    def test_append_only_log_rejects_update_delete_but_allows_append_and_insert(self):
        with tenant_session(DSN, role="app_rw", tenant_id=TENANT) as conn:
            log = Repository(conn, "rule_eval_log")
            appended = log.append(
                tenant_id=TENANT, agent="cs045-test", result="PASS", notes="append rollback"
            )
            inserted = log.insert(
                tenant_id=TENANT, agent="cs045-test", result="PASS", notes="insert rollback"
            )
            appended_pk = {key: appended[key] for key in log.spec.pk}
            self.assertEqual("append rollback", log.get(**appended_pk)["notes"])
            pk = {key: inserted[key] for key in log.spec.pk}
            with self.assertRaises(AppendOnlyError):
                log.update(pk, notes="must not update")
            with self.assertRaises(AppendOnlyError):
                log.delete(pk)
            raise Rollback

    # spec: F45.ac6 — DB roles, rather than repository convention, enforce write rights.
    def test_app_ro_cannot_insert_while_app_rw_can(self):
        with tenant_session(DSN, role="app_ro", tenant_id=TENANT) as conn:
            with self.assertRaises(InsufficientPrivilege):
                Repository(conn, "employees").insert(**self._employee_values(940046))
            raise Rollback

        with tenant_session(DSN, role="app_rw", tenant_id=TENANT) as conn:
            inserted = Repository(conn, "employees").insert(**self._employee_values(940046))
            self.assertEqual(940046, inserted["employee_id"])
            raise Rollback

    # spec: F45.ac7 — context setup refuses autocommit, invalid roles, and empty tenant state.
    def test_session_context_rejects_invalid_session_guards(self):
        with psycopg.connect(DSN, autocommit=True) as conn:
            with self.assertRaises(RuntimeError):
                open_session_context(conn, role="app_rw", tenant_id=TENANT)

        with psycopg.connect(DSN) as conn, conn.transaction():
            with self.assertRaises(RuntimeError):
                open_session_context(conn, role="not_a_role", tenant_id=TENANT)
            with self.assertRaises(RuntimeError):
                open_session_context(conn, role="app_rw", tenant_id="")


class _ExplodingConn:
    """Any attribute access fails, so a validation error must precede cursor use."""

    def cursor(self, *args, **kwargs):
        raise AssertionError("cursor used before pagination was validated")


class _FakeSpec:
    name = "employees"
    columns = ("tenant_id", "employee_id")


class RepositoryPaginationTests(unittest.TestCase):
    """BUG-054: pagination is validated before any SQL is built or executed."""

    def _repo(self):
        return Repository(_ExplodingConn(), "employees", spec=_FakeSpec())

    def test_list_rejects_zero_and_negative_limit(self):
        for value in (0, -1, -1000):
            with self.assertRaises(ValueError) as caught:
                self._repo().list(limit=value)
            self.assertIn("limit", str(caught.exception))

    def test_list_rejects_limit_above_max(self):
        with self.assertRaises(ValueError) as caught:
            self._repo().list(limit=1001)
        self.assertIn("1000", str(caught.exception))

    def test_list_rejects_negative_offset(self):
        with self.assertRaises(ValueError) as caught:
            self._repo().list(offset=-1)
        self.assertIn("offset", str(caught.exception))

    def test_list_rejects_offset_outside_postgresql_bigint(self):
        with self.assertRaisesRegex(ValueError, "offset"):
            self._repo().list(offset=1 << 63)

    def test_list_rejects_bool_float_and_str_pagination(self):
        for kwargs in ({"limit": True}, {"limit": 10.5}, {"limit": "10"},
                       {"offset": False}, {"offset": 2.5}, {"offset": "2"}):
            with self.assertRaises(ValueError):
                self._repo().list(**kwargs)

    def test_list_validates_before_touching_the_cursor(self):
        with self.assertRaises(ValueError):
            self._repo().list(limit=-1)
        with self.assertRaises(ValueError):
            self._repo().list(offset=-1)

    def test_list_accepts_boundary_values(self):
        repo = self._repo()
        with self.assertRaises(AssertionError):
            repo.list(limit=1000, offset=0)
        with self.assertRaises(AssertionError):
            repo.list(limit=1, offset=10**9)


if __name__ == "__main__":
    unittest.main()
