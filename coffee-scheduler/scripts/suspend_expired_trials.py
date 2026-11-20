#!/usr/bin/env python3
"""Suspend elapsed trials through the narrow CS-062 auth_svc function port."""
from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

SCHEDULER_ROOT = Path(__file__).resolve().parents[1]
if str(SCHEDULER_ROOT) not in sys.path:
    sys.path.insert(0, str(SCHEDULER_ROOT))


def run(dsn: str) -> int:
    import psycopg

    now = datetime.now(timezone.utc)
    with psycopg.connect(dsn, prepare_threshold=None, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT onboarding_suspend_expired_trials(%s)", (now,))
            count = int(cur.fetchone()[0])
    logging.info("suspended %d expired trial tenant(s)", count)
    return count


def main() -> int:
    dsn = os.environ.get("AUTH_DATABASE_URL")
    if not dsn:
        logging.error("AUTH_DATABASE_URL (an auth_svc principal) is required")
        return 2
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        run(dsn)
    except Exception:
        logging.exception("expired trial suspension failed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
