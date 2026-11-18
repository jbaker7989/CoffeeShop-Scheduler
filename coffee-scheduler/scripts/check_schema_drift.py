#!/usr/bin/env python3
"""Check the CS-087 canonical schema contract (0 clean, 1 drift, 2 env error)."""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

SCHEDULER_ROOT = Path(__file__).resolve().parents[1]
if str(SCHEDULER_ROOT) not in sys.path:
    sys.path.insert(0, str(SCHEDULER_ROOT))

from data_access.soft_delete import TIER_A, TIER_C


def _section(document: str, table: str) -> str:
    match = re.search(rf"^### .*`{re.escape(table)}`.*$", document, re.MULTILINE)
    if not match:
        return ""
    following = re.search(r"^### ", document[match.end():], re.MULTILINE)
    end = match.end() + following.start() if following else len(document)
    return document[match.start():end]


def check(schema: Path, db_url: str) -> list[str]:
    document = schema.read_text(encoding="utf-8")
    errors: list[str] = []
    if "### 1.6 Soft-delete lifecycle" not in document:
        errors.append("schema doc lacks §1.6 Soft-delete lifecycle")
    for table in TIER_A:
        section = _section(document, table)
        if "deleted_at" not in section or "deleted_by" not in section:
            errors.append(f"schema doc: {table} lacks deleted_at/deleted_by")

    import psycopg
    with psycopg.connect(db_url) as conn:
        for table in TIER_A:
            columns = {r[0] for r in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name=%s", (table,)
            )}
            for column in ("deleted_at", "deleted_by"):
                if column not in columns:
                    errors.append(f"database: {table}.{column} is missing")
            index = conn.execute(
                "SELECT 1 FROM pg_indexes WHERE schemaname='public' AND tablename=%s AND indexname=%s",
                (table, f"idx_{table}_live"),
            ).fetchone()
            if index is None:
                errors.append(f"database: idx_{table}_live is missing")
            policy = conn.execute(
                "SELECT permissive, cmd FROM pg_policies WHERE schemaname='public' "
                "AND tablename=%s AND policyname='soft_delete_hidden'", (table,)
            ).fetchone()
            if policy != ("RESTRICTIVE", "SELECT"):
                errors.append(f"database: {table}.soft_delete_hidden is not restrictive SELECT")
        for table in TIER_C:
            columns = {r[0] for r in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name=%s", (table,)
            )}
            if {"deleted_at", "deleted_by"} & columns:
                errors.append(f"database: Tier-C {table} has a soft-delete marker")
            if conn.execute(
                "SELECT 1 FROM pg_indexes WHERE schemaname='public' AND tablename=%s AND indexname=%s",
                (table, f"idx_{table}_live"),
            ).fetchone() is not None:
                errors.append(f"database: Tier-C {table} has a live-row index")
            if conn.execute(
                "SELECT 1 FROM pg_policies WHERE schemaname='public' AND tablename=%s "
                "AND policyname='soft_delete_hidden'", (table,),
            ).fetchone() is not None:
                errors.append(f"database: Tier-C {table} has a soft-delete policy")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schema", type=Path, required=True)
    parser.add_argument("--db-url", default=os.environ.get("DATABASE_URL"))
    args = parser.parse_args(argv)
    if not args.db_url:
        print("environment error: --db-url or DATABASE_URL is required", file=sys.stderr)
        return 2
    try:
        errors = check(args.schema.resolve(strict=True), args.db_url)
    except Exception as exc:  # connection/import/path failure, not detected drift
        print(f"environment error: {exc}", file=sys.stderr)
        return 2
    if errors:
        print("schema drift detected:", *errors, sep="\n", file=sys.stderr)
        return 1
    print("schema drift check clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
