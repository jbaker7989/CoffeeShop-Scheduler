"""F61.ac7: expected database invite conflicts must map to the typed conflict path."""
from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

SCHEDULER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCHEDULER_ROOT))

from auth.repository import DatabaseAuthRepository, InvitationRecord


class _FailingCursor:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, *_args):
        raise self.error


class _FailingConnection:
    def __init__(self, error: Exception) -> None:
        self.error = error
        self.autocommit = True

    def cursor(self, **_kwargs):
        return _FailingCursor(self.error)


def _database_error(class_name: str, sqlstate: str, message: str) -> Exception:
    error_type = type(
        class_name,
        (Exception,),
        {"sqlstate": sqlstate, "diag": SimpleNamespace(message_primary=message)},
    )
    return error_type(message)


class InvitationConflictRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        now = datetime.now(UTC)
        self.invitation = InvitationRecord(
            id="00000000-0000-0000-0000-000000000010",
            tenant_id="00000000-0000-0000-0000-000000000001",
            email="new@example.test",
            role="staff",
            token_digest="digest",
            invited_by=None,
            status="pending",
            expires_at=now + timedelta(hours=1),
        )

    # spec: F61.ac7 — ordinary member/pending conflicts map into the established 409 path.
    def test_known_plpgsql_conflicts_are_normalized(self) -> None:
        for message in ("already a member", "pending invitation exists"):
            with self.subTest(message=message):
                error = _database_error("RaiseException", "P0001", message)
                repo = DatabaseAuthRepository(_FailingConnection(error))
                with self.assertRaisesRegex(ValueError, "invitation conflict"):
                    repo.create_invitation(self.invitation)

    # spec: F61.ac7 — the unique index remains the race-safe conflict backstop.
    def test_unique_violation_is_normalized(self) -> None:
        error = _database_error("UniqueViolation", "23505", "duplicate key")
        repo = DatabaseAuthRepository(_FailingConnection(error))
        with self.assertRaisesRegex(ValueError, "invitation conflict"):
            repo.create_invitation(self.invitation)

    # spec: F61.ac7 — unrelated DB errors must not be mislabeled as HTTP 409.
    def test_unrelated_and_unknown_plpgsql_errors_propagate(self) -> None:
        errors = (
            _database_error("RaiseException", "P0001", "unexpected function failure"),
            _database_error("OperationalError", "08006", "connection lost"),
        )
        for error in errors:
            with self.subTest(error=error.sqlstate):
                repo = DatabaseAuthRepository(_FailingConnection(error))
                with self.assertRaises(type(error)) as caught:
                    repo.create_invitation(self.invitation)
                self.assertIs(caught.exception, error)


if __name__ == "__main__":
    unittest.main()
