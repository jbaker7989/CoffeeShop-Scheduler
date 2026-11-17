"""Static migration graph/reversibility contract for CS-061c."""
import ast
import sys
import unittest
from pathlib import Path
SCHEDULER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCHEDULER_ROOT))
MIGRATION=SCHEDULER_ROOT/"db/migrations/versions/0012_cs061c_invitations_audit.py"
class InvitationDeploymentContractTests(unittest.TestCase):
    def test_linear_head_and_reversible_objects(self):
        tree=ast.parse(MIGRATION.read_text()); values={n.targets[0].id:n.value.value for n in tree.body if isinstance(n,ast.Assign) and isinstance(n.targets[0],ast.Name) and isinstance(n.value,ast.Constant)}
        self.assertEqual("0012",values["revision"]); self.assertEqual("0011",values["down_revision"])
        text=MIGRATION.read_text(); self.assertIn("tenant_invitations",text); self.assertIn("audit_log",text)
        self.assertIn("DROP FUNCTION IF EXISTS auth_record_audit",text); self.assertIn("DROP TABLE IF EXISTS audit_log, tenant_invitations",text)
        self.assertIn("GRANT INSERT, SELECT ON audit_log TO app_rw",text); self.assertIn("REVOKE ALL ON FUNCTION",text)
if __name__ == "__main__": unittest.main()
