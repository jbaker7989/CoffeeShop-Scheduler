"""BUG-063 — the test-first guard must see the coffee-scheduler/ repo layout.

`git log --name-only` reports repo-root-relative paths (`coffee-scheduler/contracts/x.py`), but
the classifier prefixes omit that segment, so before the fix every path was classified as
non-production and the guard passed vacuously. These tests drive the guard's pure functions with
synthetic, repo-root-relative paths — no real git repo needed — so they are fast and deterministic.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "ci"))

import check_test_first as guard  # noqa: E402

SUBDIR = "coffee-scheduler/"


class NormalizeTests(unittest.TestCase):
    # spec: BUG-063.ac1
    def test_strips_the_repo_subdir(self):
        self.assertEqual(guard._normalize("coffee-scheduler/contracts/forecast.py", SUBDIR),
                         "contracts/forecast.py")

    def test_empty_subdir_is_a_no_op(self):
        self.assertEqual(guard._normalize("contracts/forecast.py", ""), "contracts/forecast.py")

    def test_path_outside_subtree_is_unchanged(self):
        self.assertEqual(guard._normalize("README.md", SUBDIR), "README.md")

    # spec: BUG-063.ac1 (the regression: these matched nothing before the fix)
    def test_production_and_test_classify_after_normalisation(self):
        for raw in ("coffee-scheduler/contracts/forecast.py", "coffee-scheduler/db/x.py",
                    "coffee-scheduler/api/app.py", "coffee-scheduler/agents/scheduler/solve.py"):
            self.assertTrue(guard._is_production(guard._normalize(raw, SUBDIR)), raw)
        self.assertTrue(guard._is_test(guard._normalize("coffee-scheduler/test/test_x.py", SUBDIR)))
        # Pre-fix behaviour: the un-normalised path matched neither classifier.
        self.assertFalse(guard._is_production("coffee-scheduler/contracts/forecast.py"))


def _commit(sha: str, *paths: str) -> tuple[str, list[str]]:
    return (sha * 40)[:40], list(paths)


class EvaluateOrderingTests(unittest.TestCase):
    # spec: BUG-063.ac2
    def test_production_before_test_fails(self):
        commits = [
            _commit("a", "coffee-scheduler/contracts/forecast.py"),
            _commit("b", "coffee-scheduler/test/test_forecast.py"),
        ]
        code, message = guard.evaluate(commits, SUBDIR)
        self.assertEqual(code, 1, message)
        self.assertIn("FAIL", message)

    # spec: BUG-063.ac2
    def test_test_before_production_passes(self):
        commits = [
            _commit("a", "coffee-scheduler/test/test_forecast.py"),
            _commit("b", "coffee-scheduler/contracts/forecast.py"),
        ]
        code, message = guard.evaluate(commits, SUBDIR)
        self.assertEqual(code, 0, message)
        self.assertIn("OK", message)

    def test_test_and_production_in_same_commit_passes(self):
        commits = [_commit("a", "coffee-scheduler/test/test_forecast.py",
                           "coffee-scheduler/contracts/forecast.py")]
        code, _ = guard.evaluate(commits, SUBDIR)
        self.assertEqual(code, 0)

    def test_production_without_any_test_fails(self):
        commits = [_commit("a", "coffee-scheduler/api/app.py")]
        code, message = guard.evaluate(commits, SUBDIR)
        self.assertEqual(code, 1)
        self.assertIn("no test file", message)

    # spec: BUG-063.ac3
    def test_excluded_prefixes_are_neither_test_nor_production(self):
        commits = [_commit("a",
                           "coffee-scheduler/db/migrations/versions/0099_x.py",
                           "coffee-scheduler/docs/plan.md",
                           "coffee-scheduler/scripts/ci/tool.py")]
        code, message = guard.evaluate(commits, SUBDIR)
        self.assertEqual(code, 0, message)
        self.assertIn("nothing to order", message)


if __name__ == "__main__":
    unittest.main()
