"""Exercise both CI commands in a checkout with the same nested project layout."""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class ProjectPathTests(unittest.TestCase):
    # spec: F6.ac6 — local edits and committed PR changes both reach the CI gates.
    def test_changed_test_in_nested_project(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            project = repo / "scheduler"
            ci = project / "scripts/ci"
            shutil.copytree(Path(__file__).resolve().parents[1] / "scripts/ci", ci)
            (project / "test").mkdir()
            (project / "FEATURES.md").write_text("### F6. CI\n- **AC6** — inspect changed tests.\n")
            test = project / "test/test_example with space.py"
            test.write_text("")
            sibling = repo / "sibling.py"
            sibling.write_text("")

            def git(*args):
                subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)

            def check(script, expected, *args):
                run = subprocess.run([sys.executable, ci / script, *args], cwd=repo,
                                     capture_output=True, text=True)
                self.assertEqual(run.returncode, int(bool(expected)), run.stderr)
                if expected:
                    self.assertIn(expected, run.stderr)

            def commit(message):
                git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.test",
                    "commit", "-am", message)

            git("init")
            git("add", ".")
            commit("base")
            git("tag", "base")
            sibling.write_text("# TODO: ignored outside scheduler\n")
            test.write_text("# TODO: check fixture\n")
            for args in ((), ("--base", "base")):
                if args:
                    commit("changed")
                check("check_spec_traceability.py", "untagged test file: test/test_example with space.py", *args)
                check("review_checklist.py", "test/test_example with space.py:1", *args)

            test.write_text("# x = 1\n# y = 2\n# z = 3")
            check("review_checklist.py", "commented-out code block")

            test.write_text('# spec: F6.ac6\nmessage = "TODO marker in ordinary text"\n')
            commit("fix")
            check("check_spec_traceability.py", "", "--base", "base")
            check("review_checklist.py", "", "--base", "base")


if __name__ == "__main__":
    unittest.main()
