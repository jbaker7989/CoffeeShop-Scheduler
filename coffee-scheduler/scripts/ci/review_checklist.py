"""Review-checklist script — test/SKILL.md CI job 3d.

Greps the files this change touches for the machine-checkable rows of the human review
checklist. The rows that need judgement (spec completeness, style consistency, comment
quality) stay human; these are the ones a grep can settle.

Checks:
  1. A `TODO` with no linked issue (`#123`, `BUG-123`, `CS-123`, or a URL).
  2. A hardcoded secret: a literal assigned to a name that looks like a credential.
  3. A commented-out code block: three or more consecutive `#` lines that parse as Python.

Usage:
    python scripts/ci/review_checklist.py [--base <git-ref>]
"""
from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

TODO = re.compile(r"\bTODO\b")
TODO_LINK = re.compile(r"(#\d+|\bBUG-\d+\b|\bCS-\d+\b|https?://\S+)")
SECRET_NAME = re.compile(
    r"\b(password|passwd|secret|token|api[_-]?key|private[_-]?key|pepper|client[_-]?secret)\b",
    re.IGNORECASE,
)
SECRET_LITERAL = re.compile(r"""=\s*["'][^"'\s]{8,}["']""")
SCAN_SUFFIXES = (".py", ".yml", ".yaml", ".toml", ".sh", ".ini", ".sql")


def _changed_files(base: str | None) -> list[str]:
    cmd = ["git", "diff", "--name-only", f"{base}...HEAD"] if base else ["git", "diff", "--name-only", "HEAD"]
    result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise SystemExit(f"git diff failed: {result.stderr.strip()}")
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _commented_code_block(lines: list[str]) -> int | None:
    """Return the 1-based line number where a 3+ line commented-out code block starts."""
    run: list[str] = []
    start = 0
    for number, line in enumerate(lines, start=1):
        stripped = line.lstrip()
        if stripped.startswith("#") and not stripped.startswith("#!"):
            body = stripped.lstrip("#").strip()
            if body and not body.startswith(("-", "=", "*")):
                if not run:
                    start = number
                run.append(body)
                continue
        if len(run) >= 3:
            try:
                ast.parse("\n".join(run))
            except SyntaxError:
                pass
            else:
                return start
        run = []
    if len(run) >= 3:
        try:
            ast.parse("\n".join(run))
        except SyntaxError:
            pass
        else:
            return start
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default=None, help="git ref to diff against; omit to diff HEAD")
    args = parser.parse_args(argv)

    failures: list[str] = []
    for path in _changed_files(args.base):
        if not path.endswith(SCAN_SUFFIXES):
            continue
        target = ROOT / path
        if not target.exists():
            continue
        lines = target.read_text(encoding="utf-8", errors="ignore").splitlines()

        for number, line in enumerate(lines, start=1):
            if TODO.search(line) and not TODO_LINK.search(line):
                failures.append(f"{path}:{number} — TODO without a linked issue")
            if SECRET_NAME.search(line) and SECRET_LITERAL.search(line):
                failures.append(f"{path}:{number} — possible hardcoded secret")

        if path.endswith(".py"):
            start = _commented_code_block(lines)
            if start is not None:
                failures.append(f"{path}:{start} — commented-out code block")

    if failures:
        print(f"FAIL — {len(failures)} review-checklist defect(s):", file=sys.stderr)
        for item in failures:
            print(f"  - {item}", file=sys.stderr)
        return 1
    print("OK — no machine-checkable review defects in the changed files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
