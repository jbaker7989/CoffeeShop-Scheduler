from __future__ import annotations

import inspect
import sys
import unittest
from pathlib import Path

SCHEDULER_ROOT = Path(__file__).resolve().parents[1]
if str(SCHEDULER_ROOT) not in sys.path:
    sys.path.insert(0, str(SCHEDULER_ROOT))


class _Connection:
    def __init__(self, pool):
        self.pool = pool

    def __enter__(self):
        self.pool.entered += 1
        return self

    def __exit__(self, exc_type, exc, tb):
        self.pool.exited += 1
        return False


class _Pool:
    def __init__(self, closed=False):
        self.closed = closed
        self.opened = 0
        self.entered = 0
        self.exited = 0

    def open(self):
        self.opened += 1
        self.closed = False

    def connection(self):
        return _Connection(self)


class _DatabaseRepo:
    def __init__(self, conn):
        self.conn = conn

    def access_state(self, *args, **kwargs):
        if args == ("tenant", "user", "jti", "iat") and kwargs == {"marker": True}:
            raise RuntimeError("delegated failure")
        return args, kwargs


class PooledAdapterDelegationTests(unittest.TestCase):
    def test_shared_call_opens_pool_borrows_once_and_forwards(self):
        from api.auth_gateway import PooledAuthRepository

        pool = _Pool(closed=True)
        repository = PooledAuthRepository(pool)
        repository._repo = lambda conn: _DatabaseRepo(conn)
        result = repository.access_state("tenant", "user", "jti", "iat")
        self.assertEqual((("tenant", "user", "jti", "iat"), {}), result)
        self.assertEqual((1, 1, 1), (pool.opened, pool.entered, pool.exited))
        self.assertFalse(pool.closed)

    def test_connection_is_released_when_delegate_raises(self):
        from api.auth_gateway import PooledAuthRepository

        pool = _Pool()
        repository = PooledAuthRepository(pool)
        repository._repo = lambda conn: _DatabaseRepo(conn)
        with self.assertRaisesRegex(RuntimeError, "delegated failure"):
            repository._call("access_state", "tenant", "user", "jti", "iat", marker=True)
        self.assertEqual((1, 1), (pool.entered, pool.exited))

    def test_public_adapter_method_has_explicit_signature(self):
        from api.auth_gateway import PooledAuthRepository

        parameters = inspect.signature(PooledAuthRepository.access_state).parameters
        self.assertEqual(["self", "tenant_id", "user_id", "jti", "issued_at"], list(parameters))


