"""Spec lint — test/SKILL.md CI job 3a.

Every AC in FEATURES.md must be a single testable assertion. Fails on hedges that make a
criterion untestable: "etc.", "and so on", "and more", "TODO", "TBD".

An ellipsis is deliberately not a hedge: it is used as a value placeholder in SQL and
config examples (e.g. `SET LOCAL app.tenant_id = …`), so flagging it is noise.

Runs over the whole file, not just the diff: a hedge is cheap to fix and expensive to
leave in the spec.

Usage:
    python scripts/ci/spec_lint.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FEATURES = ROOT / "FEATURES.md"

AC_LINE = re.compile(r"^\s*\d+\.\s+\*\*AC\d+")
HEDGES = (
    (re.compile(r"\betc\.", re.IGNORECASE), "etc."),
    (re.compile(r"\band so on\b", re.IGNORECASE), "and so on"),
    (re.compile(r"\band more\b", re.IGNORECASE), "and more"),
    (re.compile(r"\bTODO\b"), "TODO"),
    (re.compile(r"\bTBD\b"), "TBD"),
)


def main() -> int:
    text = FEATURES.read_text(encoding="utf-8")
    failures: list[str] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not AC_LINE.match(line):
            continue
        for pattern, label in HEDGES:
            if pattern.search(line):
                failures.append(f"FEATURES.md:{number} — AC contains {label!r}: {line.strip()[:100]}")

    if failures:
        print(f"FAIL — {len(failures)} untestable AC(s):", file=sys.stderr)
        for item in failures:
            print(f"  - {item}", file=sys.stderr)
        return 1
    print("OK — every AC is a single testable assertion.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
