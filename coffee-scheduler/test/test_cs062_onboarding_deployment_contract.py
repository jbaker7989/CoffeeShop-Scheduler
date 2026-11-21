"""Static deployment boundary for reversible CS-062 migration."""
from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

SCHEDULER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCHEDULER_ROOT))


class OnboardingDeploymentContractTests(unittest.TestCase):
    def test_0016_is_the_single_linear_head_and_reverses_every_object(self) -> None:
        versions = SCHEDULER_ROOT / "db" / "migrations" / "versions"
        migration = versions / "0016_cs062_onboarding.py"
        self.assertTrue(migration.is_file())
        tree = ast.parse(migration.read_text(encoding="utf-8"))
        values = {node.targets[0].id: ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and node.targets[0].id in {"revision", "down_revision"}}
        self.assertEqual({"revision": "0016", "down_revision": "0015"}, values)
        revisions = {}
        for path in versions.glob("*.py"):
            parsed = ast.parse(path.read_text(encoding="utf-8"))
            pairs = {node.targets[0].id: ast.literal_eval(node.value) for node in parsed.body if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and node.targets[0].id in {"revision", "down_revision"}}
            if "revision" in pairs: revisions[pairs["revision"]] = pairs.get("down_revision")
        # Linearity invariant: exactly one head (not pinned to a revision number, so a
        # later migration such as 0017 extending the chain does not falsely fail this).
        self.assertEqual(1, len([revision for revision in revisions if revision not in revisions.values()]))
        source = migration.read_text(encoding="utf-8")
        for fragment in (
            "CREATE TABLE onboarding_verification_tokens", "CREATE TABLE labor_rule_templates",
            "CREATE FUNCTION onboarding_signup", "CREATE FUNCTION onboarding_issue_verification",
            "CREATE FUNCTION onboarding_verify_email", "CREATE FUNCTION onboarding_suspend_expired_trials",
            "CREATE FUNCTION onboarding_provision", "SECURITY DEFINER SET search_path = pg_catalog, public",
            "REVOKE ALL ON FUNCTION", "GRANT EXECUTE ON FUNCTION", "t.status IN ('active','trial')",
            "DROP FUNCTION IF EXISTS onboarding_provision", "DROP TABLE IF EXISTS onboarding_verification_tokens",
            "DROP TABLE IF EXISTS labor_rule_templates", "DROP COLUMN IF EXISTS provisioned_at",
            "DROP COLUMN IF EXISTS trial_ends_at", "DROP COLUMN IF EXISTS email_verified_at", "t.status = 'active'",
        ):
            self.assertIn(fragment, source)


if __name__ == "__main__":
    unittest.main()
