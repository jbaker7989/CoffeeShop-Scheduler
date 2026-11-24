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


def _repo_subdir() -> str:
    """The path of this project within the git repo, e.g. ``coffee-scheduler/`` (``""`` if root).

    ``git log --name-only`` reports paths relative to the repository root, but this project lives
    in a subdirectory, so those paths carry a leading ``coffee-scheduler/`` that the classifier
    prefixes omit. Discovering the subdir lets us strip it before matching. Fail-closed: if git
    cannot report it, raise so the caller surfaces the problem rather than silently matching
    nothing.
    """
    result = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "--show-prefix"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(f"git rev-parse --show-prefix failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _normalize(path: str, subdir: str) -> str:
    """Strip the project's repo subdirectory so classifier prefixes match.

    A path outside the subtree does not start with ``subdir`` and is returned unchanged; it will
    not match the test/production prefixes, so the guard stays scoped to this project's tree.
    """
    if subdir and path.startswith(subdir):
        return path[len(subdir):]
    return path


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


def evaluate(commits: list[tuple[str, list[str]]], subdir: str) -> tuple[int, str]:
    """Decide the test-first ordering for already-collected commits.

    Pure (no git, no I/O) so it is directly testable: paths are normalised by ``subdir`` and then
    classified. Returns ``(returncode, message)`` where a non-zero code means the ordering is
    violated; the message is printed by ``main``.
    """
    first_test: tuple[int, str] | None = None
    first_prod: tuple[int, str] | None = None
    for index, (sha, paths) in enumerate(commits):
        normalized = [_normalize(p, subdir) for p in paths]
        if first_test is None and any(_is_test(p) for p in normalized):
            first_test = (index, sha[:8])
        if first_prod is None and any(_is_production(p) for p in normalized):
            first_prod = (index, sha[:8])

    if first_prod is None:
        return 0, "OK — no production files added; nothing to order."
    if first_test is None:
        return 1, f"FAIL — production code added at {first_prod[1]} but no test file was added on this branch."
    if first_test[0] > first_prod[0]:
        return 1, (
            f"FAIL — production code added at commit {first_prod[1]} before the first test at {first_test[1]}."
        )
    return 0, f"OK — first test at {first_test[1]} precedes first production code at {first_prod[1]}."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="origin/main", help="base ref to compare against")
    args = parser.parse_args(argv)

    try:
        commits = _added_files_by_commit(args.base)
        subdir = _repo_subdir()
    except SystemExit as exc:
        print(f"SKIP — cannot resolve base ref {args.base!r}: {exc}", file=sys.stderr)
        return 0

    code, message = evaluate(commits, subdir)
    print(message, file=sys.stderr if code else sys.stdout)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
