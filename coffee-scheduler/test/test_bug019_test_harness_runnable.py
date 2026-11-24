"""BUG-019 — every test module must actually run its tests when invoked as a script.

A test file that defines test classes but has no ``if __name__ == "__main__":`` entrypoint exits
with zero tests run when invoked as `python3 test/test_x.py`, so its regressions silently guard
nothing in the documented script workflow. This guard keeps that defect from recurring, and
proves the named BUG-019 file (`test_rules_admin.py`) now runs.
"""

from __future__ import annotations

import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEST_DIR = ROOT / "test"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class TestHarnessRunnableTests(unittest.TestCase):
    # spec: BUG-019.ac2
    def test_every_test_module_has_a_main_entrypoint(self):
        missing = [
            path.name
            for path in sorted(TEST_DIR.glob("test_*.py"))
            if '__name__ == "__main__"' not in path.read_text()
            and "__name__ == '__main__'" not in path.read_text()
        ]
        self.assertEqual(
            missing, [],
            f"these test modules define tests but never run them as a script: {missing}",
        )

    # spec: BUG-019.ac1
    def test_rules_admin_runs_as_a_script(self):
        proc = subprocess.run(
            [sys.executable, str(TEST_DIR / "test_rules_admin.py")],
            capture_output=True, text=True, cwd=str(ROOT), check=False,
        )
        output = proc.stdout + proc.stderr
        match = re.search(r"Ran (\d+) tests?", output)
        self.assertIsNotNone(match, f"no test-run summary in output:\n{output}")
        self.assertGreater(int(match.group(1)), 0, "script run executed zero tests")
        # Non-DB tests pass and DB-gated tests skip, so a correct run exits zero.
        self.assertEqual(proc.returncode, 0, f"script run failed:\n{output}")


if __name__ == "__main__":
    unittest.main()
