#!/usr/bin/env python3
"""CI guard for the CS-087 no-hard-delete rule (0 clean, 1 violation, 2 env error)."""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

SCHEDULER_ROOT = Path(__file__).resolve().parents[1]
if str(SCHEDULER_ROOT) not in sys.path:
    sys.path.insert(0, str(SCHEDULER_ROOT))

try:
    from data_access.soft_delete import TIER_A, TIER_B
except ImportError as exc:  # pragma: no cover - only an invalid checkout/import path
    print(f"environment error: cannot load soft-delete policy catalog: {exc}", file=sys.stderr)
    raise SystemExit(2)


DOMAIN_TABLES = frozenset((*TIER_A, *TIER_B))
# Tier C is deliberately absent: credential expiry/pruning and immutable/log lifecycle
# maintenance are not domain-record deletion.  The F69 erasure module is reserved here.
ALLOWLISTED_F69_ERASURE = Path("compliance/f69_erasure.py")
SCAN_DIRS = ("db/migrations", "data_access", "api", "auth", "compliance", "scripts")
DELETE_RE = re.compile(r"\bDELETE\s+FROM\s+(?:public\.)?[\"']?([a-z_][a-z0-9_]*)", re.I)
DROP_RE = re.compile(r"\bDROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:public\.)?[\"']?([a-z_][a-z0-9_]*)", re.I)


def violations(root: Path) -> list[str]:
    found: list[str] = []
    for relative in SCAN_DIRS:
        directory = root / relative
        if not directory.exists():
            continue
        if not directory.is_dir():
            raise RuntimeError(f"scan target is not a directory: {directory}")
        for path in directory.rglob("*"):
            if not path.is_file() or "test" in path.parts:
                continue
            if path.relative_to(root) == ALLOWLISTED_F69_ERASURE:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            for number, line in enumerate(text.splitlines(), 1):
                for match in (*DELETE_RE.finditer(line), *DROP_RE.finditer(line)):
                    if match.group(1).lower() in DOMAIN_TABLES:
                        found.append(f"{path.relative_to(root)}:{number}: {line.strip()}")
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    try:
        root = args.root.resolve(strict=True)
        found = violations(root)
    except (OSError, RuntimeError) as exc:
        print(f"environment error: {exc}", file=sys.stderr)
        return 2
    if found:
        print("hard-delete policy violations:", file=sys.stderr)
        print("\n".join(found), file=sys.stderr)
        return 1
    print("no hard-delete policy violations found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
