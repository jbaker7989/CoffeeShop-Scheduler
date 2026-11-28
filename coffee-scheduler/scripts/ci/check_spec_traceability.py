"""Spec-ID traceability gate — test/SKILL.md CI job 1.

Fails closed on three defects:
  1. A `spec: F<id>.ac<n>` tag in a test file names an AC that FEATURES.md does not define.
  2. A test file touched by this change carries no spec tag at all.
  3. A story marked Done has an AC with no test, unless the AC is listed in known_gaps.txt.

The gate is a ratchet: rule 2 applies only to files this change touches, so pre-existing
untagged files do not block the pipeline. Rules 1 and 3 apply repo-wide, with
known_gaps.txt holding the debt already accepted when the gate was introduced.

ACs are defined in FEATURES.md and in the per-story SDD documents under docs/sdd-*.md;
both are parsed, because umbrella stories (F42, F45) carry their ACs only in the SDD.

Usage:
    python scripts/ci/check_spec_traceability.py [--base <git-ref>]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from changed_files import changed_files

ROOT = Path(__file__).resolve().parents[2]
FEATURES = ROOT / "FEATURES.md"
TEST_DIR = ROOT / "test"
KNOWN_GAPS = Path(__file__).resolve().parent / "known_gaps.txt"

SPEC_TAG = re.compile(r"spec:\s*(F\d+\.ac\d+)", re.IGNORECASE)
STORY_HEAD = re.compile(r"^###\s+(F\d+)\b", re.MULTILINE)
AC_HEAD = re.compile(r"\*\*AC(\d+)\b")
SDD_AC_ID = re.compile(r"\*\*(F\d+\.ac\d+)\*\*", re.IGNORECASE)


def _features_ac_ids() -> set[str]:
    """Return every AC id defined in FEATURES.md or a docs/sdd-*.md document."""
    text = FEATURES.read_text(encoding="utf-8")
    ids: set[str] = set()
    for block in re.split(r"^###\s+", text, flags=re.MULTILINE)[1:]:
        head = STORY_HEAD.match("### " + block)
        if not head:
            continue
        story = head.group(1)
        for ac in AC_HEAD.findall(block):
            ids.add(f"{story}.ac{ac}".lower())
    for sdd in sorted((ROOT / "docs").glob("sdd-*.md")):
        ids.update(m.lower() for m in SDD_AC_ID.findall(sdd.read_text(encoding="utf-8", errors="ignore")))
    return ids


def _done_story_ac_ids() -> dict[str, set[str]]:
    """Return AC ids grouped by story, for stories whose Status line says Done."""
    text = FEATURES.read_text(encoding="utf-8")
    out: dict[str, set[str]] = {}
    for block in re.split(r"^###\s+", text, flags=re.MULTILINE)[1:]:
        head = STORY_HEAD.match("### " + block)
        if not head:
            continue
        story = head.group(1)
        preamble = block.split("\n---", 1)[0]
        if not re.search(r"Status:.*\bDone\b", preamble):
            continue
        out[story] = {f"{story}.ac{ac}".lower() for ac in AC_HEAD.findall(block)}
    return out


def _test_tags() -> dict[str, set[str]]:
    """Return spec tags per test file."""
    tags: dict[str, set[str]] = {}
    for path in sorted(TEST_DIR.glob("test_*.py")):
        found = {m.lower() for m in SPEC_TAG.findall(path.read_text(encoding="utf-8", errors="ignore"))}
        tags[str(path.relative_to(ROOT))] = found
    return tags


def _known_gaps() -> set[str]:
    if not KNOWN_GAPS.exists():
        return set()
    return {
        line.split("#", 1)[0].strip().lower()
        for line in KNOWN_GAPS.read_text(encoding="utf-8").splitlines()
        if line.split("#", 1)[0].strip()
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default=None, help="git ref to diff against; omit to use HEAD")
    args = parser.parse_args(argv)

    defined = _features_ac_ids()
    tags_by_file = _test_tags()
    all_tags = set().union(*tags_by_file.values()) if tags_by_file else set()
    failures: list[str] = []

    gaps = _known_gaps()

    # Rule 1 — dangling tags.
    for dangling in sorted(all_tags - defined - gaps):
        failures.append(f"dangling spec tag: {dangling} is not an AC in FEATURES.md or docs/sdd-*.md")

    # Rule 2 — touched test files must carry a spec tag.
    for path in changed_files(ROOT, args.base):
        if not path.startswith("test/") or not Path(path).match("test_*.py"):
            continue
        if not Path(ROOT / path).exists():
            continue  # deleted file
        if not tags_by_file.get(path):
            failures.append(f"untagged test file: {path} has no 'spec: F<id>.ac<n>' tag")

    # Rule 3 — Done stories must be fully covered.
    for story, acs in sorted(_done_story_ac_ids().items()):
        for missing in sorted(acs - all_tags - gaps):
            failures.append(f"uncovered AC: {missing} ({story} is Done) has no test")

    if failures:
        print(f"FAIL — {len(failures)} traceability defect(s):", file=sys.stderr)
        for item in failures:
            print(f"  - {item}", file=sys.stderr)
        return 1

    print(f"OK — {len(defined)} ACs defined, {len(all_tags)} tagged, all Done stories covered.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
