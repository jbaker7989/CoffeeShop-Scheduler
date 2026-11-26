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
import io
import re
import sys
import tokenize
from itertools import chain
from pathlib import Path

from changed_files import changed_files

ROOT = Path(__file__).resolve().parents[2]

TODO = re.compile(r"\bTODO\b")
TODO_LINK = re.compile(r"(#\d+|\bBUG-\d+\b|\bCS-\d+\b|https?://\S+)")
SECRET_NAME = re.compile(
    r"\b(password|passwd|secret|token|api[_-]?key|private[_-]?key|pepper|client[_-]?secret)\b",
    re.IGNORECASE,
)
SECRET_LITERAL = re.compile(r"""=\s*["'][^"'\s]{8,}["']""")
SCAN_SUFFIXES = (".py", ".yml", ".yaml", ".toml", ".sh", ".ini", ".sql")


def _commented_code_block(lines: list[str]) -> int | None:
    """Return the 1-based line number where a 3+ line commented-out code block starts."""
    run: list[str] = []
    start = 0
    # A final blank line flushes a comment block that reaches EOF.
    for number, line in enumerate(chain(lines, ("",)), start=1):
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
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default=None, help="git ref to diff against; omit to diff HEAD")
    args = parser.parse_args(argv)

    failures: list[str] = []
    for path in changed_files(ROOT, args.base):
        if not path.endswith(SCAN_SUFFIXES):
            continue
        target = ROOT / path
        if not target.exists():
            continue
        lines = target.read_text(encoding="utf-8", errors="ignore").splitlines()

        if path.endswith(".py"):
            tokens = tokenize.generate_tokens(io.StringIO("\n".join(lines)).readline)
            notes = ((token.start[0], token.string) for token in tokens if token.type == tokenize.COMMENT)
        else:
            notes = enumerate(lines, start=1)
        for number, note in notes:
            if TODO.search(note) and not TODO_LINK.search(note):
                failures.append(f"{path}:{number} — TODO without a linked issue")

        for number, line in enumerate(lines, start=1):
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
