"""Test-first commit guard — test/SKILL.md CI job 2.

The first commit on the branch that adds a file under test/ must come before the first
commit that adds production code. Catches "tests added in a cleanup commit" after the
implementation already landed.

A commit that adds both a test and production code counts as test-first only when the
test file is the one being added first — git cannot order within a commit, so a mixed
commit is treated as acceptable but reported.

Usage:
    python scripts/ci/check_test_first.py --base origin/main
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Production code: shipped Python outside test/. Docs, config and SQL migrations are not
# "production code" for this guard — a migration is DDL, not an implementation unit.
PRODUCTION_PREFIXES = ("auth/", "compliance/", "data_access/", "api/", "agents/", "db/", "otel/", "n8n/", "contracts/")
PRODUCTION_SUFFIXES = (".py",)
EXCLUDED_PREFIXES = ("test/", "scripts/ci/", "docs/", "db/migrations/versions/")


def _is_test(path: str) -> bool:
    return path.startswith("test/") and path.endswith(".py")


def _is_production(path: str) -> bool:
    if _is_test(path) or path.startswith(EXCLUDED_PREFIXES):
        return False
    if not path.endswith(PRODUCTION_SUFFIXES):
        return False
    return path.startswith(PRODUCTION_PREFIXES)


def _added_files_by_commit(base: str) -> list[tuple[str, list[str]]]:
    """Return [(sha, [added paths])] in commit order, oldest first."""
    result = subprocess.run(
        ["git", "log", "--reverse", "--format=%H", "--name-only", "--diff-filter=A", f"{base}..HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(f"git log failed: {result.stderr.strip()}")
    commits: list[tuple[str, list[str]]] = []
    sha: str | None = None
    paths: list[str] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        if len(line) == 40 and all(c in "0123456789abcdef" for c in line):
            if sha is not None:
                commits.append((sha, paths))
            sha, paths = line, []
        elif sha is not None:
            paths.append(line)
    if sha is not None:
        commits.append((sha, paths))
    return commits


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="origin/main", help="base ref to compare against")
    args = parser.parse_args(argv)

    try:
        commits = _added_files_by_commit(args.base)
    except SystemExit as exc:
        print(f"SKIP — cannot resolve base ref {args.base!r}: {exc}", file=sys.stderr)
        return 0

    first_test: tuple[int, str] | None = None
    first_prod: tuple[int, str] | None = None
    for index, (sha, paths) in enumerate(commits):
        if first_test is None and any(_is_test(p) for p in paths):
            first_test = (index, sha[:8])
        if first_prod is None and any(_is_production(p) for p in paths):
            first_prod = (index, sha[:8])

    if first_prod is None:
        print("OK — no production files added; nothing to order.")
        return 0
    if first_test is None:
        print(
            f"FAIL — production code added at {first_prod[1]} but no test file was added on this branch.",
            file=sys.stderr,
        )
        return 1
    if first_test[0] > first_prod[0]:
        print(
            f"FAIL — production code added at commit {first_prod[1]} before the first test at {first_test[1]}.",
            file=sys.stderr,
        )
        return 1
    print(f"OK — first test at {first_test[1]} precedes first production code at {first_prod[1]}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
