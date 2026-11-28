"""F61.ac8: invitation throttle stays behind reversible auth_svc functions."""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

SCHEDULER_ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = SCHEDULER_ROOT / "db" / "migrations" / "versions"


def _assignments(path: Path) -> dict[str, object]:
    tree = ast.parse(path.read_text())
    return {
        node.targets[0].id: node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and isinstance(node.value, ast.Constant)
    }


class InvitationThrottleMigrationTests(unittest.TestCase):
    # spec: F61.ac8 — migrations are linear, least-privilege, and remove every new object.
    def test_throttle_migration_is_reversible_and_auth_svc_only(self) -> None:
        migration = MIGRATIONS / "0022_bug043_invitation_accept_throttle.py"
        assignments = _assignments(migration)
        self.assertEqual("0022", assignments["revision"])
        self.assertEqual("0021", assignments["down_revision"])
        text = migration.read_text()
        self.assertIn("client_ip INET PRIMARY KEY", text)
        self.assertIn("failures TIMESTAMPTZ[]", text)
        self.assertIn("p_max_failures NOT BETWEEN 1 AND 100", text)
        self.assertIn("p_window_seconds NOT BETWEEN 1 AND 86400", text)
        self.assertIn("p_lockout_seconds NOT BETWEEN 1 AND 86400", text)
        self.assertIn("SECURITY DEFINER SET search_path = pg_catalog, public", text)
        self.assertIn("REVOKE ALL ON TABLE auth_invitation_accept_throttle FROM PUBLIC, app_rw, app_ro, auth_svc", text)
        self.assertIn("GRANT EXECUTE ON FUNCTION auth_reserve_invitation_accept", text)
        self.assertIn("TO auth_svc", text)
        self.assertIn("DROP FUNCTION IF EXISTS auth_complete_invitation_accept", text)
        self.assertIn("DROP FUNCTION IF EXISTS auth_reserve_invitation_accept", text)
        self.assertIn("DROP TABLE IF EXISTS auth_invitation_accept_throttle", text)

    # spec: F61.ac7 — valid redemptions avoid PL/pgSQL OUT-column name ambiguity.
    def test_acceptance_fix_qualifies_out_name_collisions_and_preserves_grant(self) -> None:
        migration = MIGRATIONS / "0023_bug041_qualify_invitation_accept.py"
        assignments = _assignments(migration)
        self.assertEqual("0023", assignments["revision"])
        self.assertEqual("0022", assignments["down_revision"])
        text = migration.read_text()
        self.assertIn("CREATE OR REPLACE FUNCTION auth_accept_invitation", text)
        self.assertIn("u.tenant_id = v_invitation.tenant_id", text)
        self.assertIn("i.id = v_invitation.id", text)
        self.assertIn("SECURITY DEFINER SET search_path = pg_catalog, public", text)
        self.assertIn("PREVIOUS_FUNCTION", text)


if __name__ == "__main__":
    unittest.main()
