"""F61.ac7/ac8 live PostgreSQL regressions (opt in with CS061B_DATABASE_INTEGRATION=1)."""
from __future__ import annotations

import os
import subprocess
import sys
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.sql import SQL, Identifier, Literal

try:
    from psycopg_pool import ConnectionPool
except ModuleNotFoundError:
    ConnectionPool = None  # type: ignore[assignment,misc]

SCHEDULER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCHEDULER_ROOT))


_DATABASE_INTEGRATION_ENABLED = bool(os.environ.get("CS061B_DATABASE_INTEGRATION"))
if _DATABASE_INTEGRATION_ENABLED and ConnectionPool is None:
    raise RuntimeError(
        "CS061B_DATABASE_INTEGRATION=1 requires psycopg_pool; install api/requirements.txt",
    )


@unittest.skipUnless(
    _DATABASE_INTEGRATION_ENABLED,
    "set CS061B_DATABASE_INTEGRATION=1 with a disposable PostgreSQL DATABASE_URL",
)
class InvitationDatabaseIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.owner_url = os.environ["DATABASE_URL"].replace(
            "postgresql+psycopg://", "postgresql://",
        )
        result = subprocess.run(
            ["alembic", "-c", str(SCHEDULER_ROOT / "db/alembic.ini"), "upgrade", "head"],
            cwd=SCHEDULER_ROOT / "db",
            env=os.environ.copy(),
            capture_output=True,
            text=True,
        )
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)

    def setUp(self) -> None:
        from auth.passwords import Argon2idPasswordService

        self.tenant_id = str(uuid.uuid4())
        self.user_id = str(uuid.uuid4())
        self.slug = f"cs061c-{uuid.uuid4().hex}"
        self.email = f"owner-{uuid.uuid4().hex}@example.test"
        self.password = "integration password"
        self.role_name = f"cs061c_{uuid.uuid4().hex}"
        self.role_password = uuid.uuid4().hex + uuid.uuid4().hex
        with psycopg.connect(self.owner_url, autocommit=True) as conn:
            conn.execute(
                SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                    Identifier(self.role_name), Literal(self.role_password),
                )
            )
            conn.execute(SQL("GRANT auth_svc TO {}").format(Identifier(self.role_name)))
            conn.execute(
                "INSERT INTO tenants(id,slug,name,status,plan,jurisdiction) "
                "VALUES(%s,%s,'integration tenant','active','starter','NY')",
                (self.tenant_id, self.slug),
            )
            password_hash = Argon2idPasswordService().hash(self.password)
            conn.execute(
                "INSERT INTO tenant_users(id,tenant_id,email,password_hash,role) "
                "VALUES(%s,%s,%s,%s,'owner')",
                (self.user_id, self.tenant_id, self.email, password_hash),
            )

        info = conninfo_to_dict(self.owner_url)
        info.update(user=self.role_name, password=self.role_password)
        self.auth_url = make_conninfo("", **info)
        self.pool_a = ConnectionPool(
            conninfo=self.auth_url,
            kwargs={"autocommit": True, "prepare_threshold": None},
            min_size=1,
            max_size=16,
            open=True,
        )
        self.pool_b = ConnectionPool(
            conninfo=self.auth_url,
            kwargs={"autocommit": True, "prepare_threshold": None},
            min_size=1,
            max_size=8,
            open=True,
        )
        self.ip_a = f"203.0.113.{uuid.uuid4().int % 240 + 10}"
        self.ip_b = f"198.51.100.{uuid.uuid4().int % 240 + 10}"
        self.ip_test = f"192.0.2.{uuid.uuid4().int % 240 + 10}"
        self.refresh_pepper = uuid.uuid4().hex * 2
        self.service_a = self._service(self.pool_a)
        self.service_b = self._service(self.pool_b)
        from fastapi.testclient import TestClient

        from api.app import create_app

        self.client_a = TestClient(
            create_app(self.service_a, fetch_stores=lambda _tenant: []),
            client=(self.ip_a, 41000),
        )
        self.client_b = TestClient(
            create_app(self.service_b, fetch_stores=lambda _tenant: []),
            client=(self.ip_a, 42000),
        )
        self.client_other_ip = TestClient(
            create_app(self.service_b, fetch_stores=lambda _tenant: []),
            client=(self.ip_b, 43000),
        )
        login = self.client_a.post(
            "/auth/login",
            json={"tenant_slug": self.slug, "email": self.email, "password": self.password},
        )
        if login.status_code != 200:
            self.fail(f"owner fixture login failed: {login.status_code} {login.text}")
        self.owner_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    def tearDown(self) -> None:
        from api.auth_gateway import PooledAuthRepository

        try:
            repo = PooledAuthRepository(self.pool_a)
            now = datetime.now(UTC)
            for ip in (self.ip_a, self.ip_b, self.ip_test):
                repo.complete_invitation_accept(ip, succeeded=True, when=now)
        finally:
            self.pool_a.close()
            self.pool_b.close()
            with psycopg.connect(self.owner_url, autocommit=True) as conn:
                conn.execute("DELETE FROM tenants WHERE id=%s", (self.tenant_id,))
                conn.execute(SQL("DROP ROLE IF EXISTS {}").format(Identifier(self.role_name)))

    def _service(self, pool: ConnectionPool):
        from api.auth_gateway import PooledAuthRepository
        from auth.passwords import Argon2idPasswordService
        from auth.service import TenantAuthService, _build_contract_token_service
        from auth.tokens import OpaqueRefreshTokenService

        return TenantAuthService(
            repository=PooledAuthRepository(pool),
            password_service=Argon2idPasswordService(),
            token_service=_build_contract_token_service(),
            refresh_token_service=OpaqueRefreshTokenService(pepper=self.refresh_pepper),
        )

    def _create_invitation(self, email: str) -> dict:
        response = self.client_a.post(
            "/invitations",
            headers=self.owner_headers,
            json={"email": email, "role": "staff"},
        )
        self.assertEqual(200, response.status_code, response.text)
        return response.json()

    # spec: F61.ac7 — P0001 guard conflicts map to HTTP 409 on the production pooled path.
    def test_duplicate_pending_and_existing_member_return_conflict(self) -> None:
        pending_email = f"pending-{uuid.uuid4().hex}@example.test"
        self._create_invitation(pending_email)
        duplicate = self.client_a.post(
            "/invitations",
            headers=self.owner_headers,
            json={"email": pending_email, "role": "staff"},
        )
        self.assertEqual(409, duplicate.status_code, duplicate.text)
        member = self.client_a.post(
            "/invitations",
            headers=self.owner_headers,
            json={"email": self.email, "role": "staff"},
        )
        self.assertEqual(409, member.status_code, member.text)

    # spec: F61.ac7 — concurrent uniqueness race maps to success + conflict, never HTTP 500.
    def test_concurrent_duplicate_invites_return_one_success_and_one_conflict(self) -> None:
        email = f"race-{uuid.uuid4().hex}@example.test"

        def create() -> int:
            return self.client_a.post(
                "/invitations",
                headers=self.owner_headers,
                json={"email": email, "role": "staff"},
            ).status_code

        with ThreadPoolExecutor(max_workers=2) as workers:
            statuses = sorted(workers.map(lambda _index: create(), range(2)))
        self.assertEqual([200, 409], statuses)

    # spec: F61.ac8 — a new app/pool observes the IP's reservation; another IP remains allowed.
    def test_pooled_gateway_throttle_persists_across_pools_and_is_ip_scoped(self) -> None:
        from auth.ratelimit import MAX_FAILED_ATTEMPTS

        issued = self._create_invitation(f"accept-{uuid.uuid4().hex}@example.test")
        for _ in range(MAX_FAILED_ATTEMPTS):
            response = self.client_a.post(
                "/invitations/accept", json={"token": "not-a-token", "password": "password"},
            )
            self.assertEqual(401, response.status_code, response.text)
        blocked = self.client_b.post(
            "/invitations/accept", json={"token": "not-a-token", "password": "password"},
        )
        self.assertEqual(429, blocked.status_code, blocked.text)
        accepted = self.client_other_ip.post(
            "/invitations/accept", json={"token": issued["token"], "password": "password"},
        )
        self.assertEqual(200, accepted.status_code, accepted.text)

    # spec: F61.ac8 — the actual successful route clears only its own durable IP bucket.
    def test_successful_gateway_accept_clears_its_ip_bucket(self) -> None:
        from auth.ratelimit import MAX_FAILED_ATTEMPTS

        invitation = self._create_invitation(f"reset-{uuid.uuid4().hex}@example.test")
        for _ in range(MAX_FAILED_ATTEMPTS - 1):
            failed = self.client_a.post(
                "/invitations/accept", json={"token": "not-a-token", "password": "password"},
            )
            self.assertEqual(401, failed.status_code, failed.text)

        redeemed = self.client_a.post(
            "/invitations/accept",
            json={"token": invitation["token"], "password": "strong integration password"},
        )
        self.assertEqual(200, redeemed.status_code, redeemed.text)

        # A further bad token remains eligible after success reset, rather than 429.
        after_reset = self.client_a.post(
            "/invitations/accept", json={"token": "not-a-token", "password": "password"},
        )
        self.assertEqual(401, after_reset.status_code, after_reset.text)

    # spec: F61.ac8 — concurrent reservations across pools cannot lose increments.
    def test_concurrent_pooled_reservations_are_serialized(self) -> None:
        from api.auth_gateway import PooledAuthRepository

        now = datetime.now(UTC)
        repos = (PooledAuthRepository(self.pool_a), PooledAuthRepository(self.pool_b))

        def reserve(index: int) -> bool:
            return repos[index % 2].begin_invitation_accept(self.ip_test, now, 5, 900, 900)

        with ThreadPoolExecutor(max_workers=12) as workers:
            results = list(workers.map(reserve, range(24)))
        self.assertEqual(5, results.count(False))
        self.assertEqual(19, results.count(True))
        self.assertTrue(repos[1].begin_invitation_accept(self.ip_test, now, 5, 900, 900))

    # spec: F61.ac8 — success clears only its IP; SQL boundaries match the memory model.
    def test_pooled_success_reset_is_ip_scoped_and_boundaries_are_exact(self) -> None:
        from api.auth_gateway import PooledAuthRepository

        repo_a = PooledAuthRepository(self.pool_a)
        repo_b = PooledAuthRepository(self.pool_b)
        now = datetime.now(UTC)
        ip_a = f"192.0.2.{uuid.uuid4().int % 240 + 10}"
        ip_b = f"198.51.100.{uuid.uuid4().int % 240 + 10}"

        # The threshold attempt is admitted while placing the bucket in lockout.
        for _ in range(3):
            self.assertFalse(repo_a.begin_invitation_accept(ip_a, now, 3, 10, 10))
            self.assertFalse(repo_a.begin_invitation_accept(ip_b, now, 3, 10, 10))
        self.assertTrue(repo_b.begin_invitation_accept(ip_a, now, 3, 10, 10))
        repo_b.complete_invitation_accept(ip_a, succeeded=True, when=now)
        self.assertFalse(repo_a.begin_invitation_accept(ip_a, now, 3, 10, 10))
        self.assertTrue(repo_a.begin_invitation_accept(ip_b, now, 3, 10, 10))

        # A locked bucket opens at the exact lockout boundary; cutoff timestamps
        # also expire at equality, matching InMemoryAuthRepository.
        boundary_ip = f"203.0.113.{uuid.uuid4().int % 240 + 10}"
        self.assertFalse(repo_a.begin_invitation_accept(boundary_ip, now, 2, 10, 10))
        self.assertFalse(repo_a.begin_invitation_accept(
            boundary_ip, now + timedelta(seconds=1), 2, 10, 10,
        ))
        self.assertFalse(repo_b.begin_invitation_accept(
            boundary_ip, now + timedelta(seconds=11), 2, 10, 10,
        ))

        invalid_configs = ((0, 10, 10), (101, 10, 10), (5, 86_401, 10), (5, 10, 86_401))
        for config in invalid_configs:
            invalid_ip = f"192.0.2.{uuid.uuid4().int % 240 + 10}"
            self.assertTrue(repo_a.begin_invitation_accept(invalid_ip, now, *config))
            with psycopg.connect(self.owner_url) as conn:
                self.assertIsNone(conn.execute(
                    "SELECT 1 FROM auth_invitation_accept_throttle WHERE client_ip=%s::inet",
                    (invalid_ip,),
                ).fetchone())

    # spec: F61.ac8 — application roles cannot bypass the SECURITY DEFINER boundary.
    def test_throttle_table_and_mutators_are_auth_svc_only(self) -> None:
        from api.auth_gateway import PooledAuthRepository

        statements = (
            ("SELECT * FROM auth_invitation_accept_throttle", ()),
            (
                "SELECT auth_reserve_invitation_accept(%s::inet,%s,%s,%s,%s)",
                (self.ip_test, datetime.now(UTC), 5, 900, 900),
            ),
        )
        for role in ("app_rw", "app_ro"):
            for statement, params in statements:
                with psycopg.connect(self.owner_url) as conn, conn.transaction():
                    conn.execute(f"SET LOCAL ROLE {role}")
                    with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                        conn.execute(statement, params)
        repo = PooledAuthRepository(self.pool_a)
        self.assertFalse(repo.begin_invitation_accept(self.ip_test, datetime.now(UTC), 5, 900, 900))


if __name__ == "__main__":
    unittest.main()
