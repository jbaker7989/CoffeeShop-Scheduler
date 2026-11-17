"""CS-061b / F61 AC3–AC4 migration and deploy contracts; red before implementation.

Requires no local database or secrets: it asserts on the migration source text, the
Alembic revision graph, and ``.env.example`` only — exactly the CS-061a deployment-contract
style (see ``test_f61_auth_deployment_contract.py``). These are red because migration
``0011_cs061b_auth_hardening.py`` and the CS-061b ``.env.example`` keys do not exist yet.

Run with ``python3 -m unittest coffee-scheduler/test/test_f61_hardening_deployment_contract.py``.
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VERSIONS = ROOT / "coffee-scheduler" / "db" / "migrations" / "versions"
HARDENING_MIGRATION = VERSIONS / "0011_cs061b_auth_hardening.py"
ENV_EXAMPLE = ROOT / ".env.example"


def migration_metadata(path: Path) -> tuple[str, str | None]:
    values: dict[str, str | None] = {}
    for statement in ast.parse(path.read_text(encoding="utf-8")).body:
        if (
            isinstance(statement, ast.Assign)
            and len(statement.targets) == 1
            and isinstance(statement.targets[0], ast.Name)
            and statement.targets[0].id in {"revision", "down_revision"}
        ):
            values[statement.targets[0].id] = ast.literal_eval(statement.value)
    return values["revision"], values["down_revision"]


class F61bMigrationGraphContractTests(unittest.TestCase):
    # spec: F61.ac3 — hardening extends the CS-061a chain as the single new head, no fork.
    def test_hardening_is_single_linear_head_after_cs061a(self) -> None:
        self.assertTrue(
            HARDENING_MIGRATION.is_file(),
            "CS-061b needs db/migrations/versions/0011_cs061b_auth_hardening.py",
        )
        self.assertEqual(("0011", "0010"), migration_metadata(HARDENING_MIGRATION))
        metadata = {path: migration_metadata(path) for path in VERSIONS.glob("*.py")}
        revisions = [revision for revision, _ in metadata.values()]
        self.assertEqual(len(revisions), len(set(revisions)), "revision ids must stay unique")
        parents = {parent for _, parent in metadata.values() if parent is not None}
        self.assertEqual({"0011"}, set(revisions) - parents, "0011 must be the one migration head")


class F61bSchemaContractTests(unittest.TestCase):
    def _source(self) -> str:
        self.assertTrue(HARDENING_MIGRATION.is_file(), "CS-061b needs migration 0011")
        return HARDENING_MIGRATION.read_text(encoding="utf-8")

    # spec: F61.ac3 — revocation storage: a per-principal session cutoff plus a per-token
    # (jti) revocation list, each FK-bound so a revocation cannot name a cross-tenant user.
    def test_revocation_storage_contract(self) -> None:
        source = self._source()
        for fragment in (
            "ALTER TABLE tenant_users",
            "sessions_revoked_at TIMESTAMPTZ",
            "CREATE TABLE auth_revoked_tokens",
            "jti UUID",
            "expires_at TIMESTAMPTZ NOT NULL",
            "FOREIGN KEY (tenant_id, user_id) REFERENCES tenant_users (tenant_id, id)",
        ):
            self.assertIn(fragment, source, fragment)

    # spec: F61.ac4 — password-reset storage: a one-way digest (never the raw secret),
    # time-box, single-use consume marker, and a cross-tenant-safe composite FK.
    def test_password_reset_storage_contract(self) -> None:
        source = self._source()
        for fragment in (
            "CREATE TABLE auth_password_reset_tokens",
            "token_digest TEXT NOT NULL UNIQUE",
            "expires_at TIMESTAMPTZ NOT NULL",
            "consumed_at TIMESTAMPTZ",
            "FOREIGN KEY (tenant_id, user_id) REFERENCES tenant_users (tenant_id, id)",
        ):
            self.assertIn(fragment, source, fragment)
        # The raw reset secret is never a column — only its peppered digest is stored.
        self.assertNotIn("reset_secret TEXT", source)

    # spec: F61.ac4 — MFA storage: encrypted secret at rest (never plaintext), a monotonic
    # replay guard, enablement limited to owner/manager, and single-use recovery digests.
    def test_mfa_storage_contract(self) -> None:
        source = self._source()
        for fragment in (
            "mfa_enabled BOOLEAN NOT NULL DEFAULT FALSE",
            "mfa_secret_ciphertext BYTEA",
            "mfa_last_timestep",
            "CREATE TABLE auth_mfa_recovery_codes",
            "code_digest TEXT NOT NULL UNIQUE",
            "role IN ('owner', 'manager')",
        ):
            self.assertIn(fragment, source, fragment)
        # No plaintext TOTP secret column may ever exist on tenant_users.
        self.assertNotIn("mfa_secret TEXT", source)

    # spec: F61.ac3 — login-throttle storage keys failures on the account AND the IP, so
    # lockout is tenant-scoped (no cross-tenant DoS) and IP abuse is bounded separately.
    def test_login_throttle_storage_contract(self) -> None:
        source = self._source()
        for fragment in (
            "CREATE TABLE auth_login_attempts",
            "ip",  # INET/text IP column for the IP-scoped throttle
            "succeeded BOOLEAN",
            "attempted_at TIMESTAMPTZ",
        ):
            self.assertIn(fragment, source, fragment)

    # spec: F61.ac4 — every CS-061b relation keeps the CS-061a least-privilege pattern:
    # owner-run SECURITY DEFINER auth_* functions, PUBLIC execute revoked, auth_svc-only,
    # RLS enabled on tenant-scoped tables, and no direct DML for app_rw/app_ro.
    def test_least_privilege_and_rls_pattern_preserved(self) -> None:
        source = self._source()
        for fragment in (
            "SECURITY DEFINER SET search_path = pg_catalog, public",
            "REVOKE ALL ON FUNCTION",
            "GRANT EXECUTE ON FUNCTION",
            "auth_svc",
            "ENABLE ROW LEVEL SECURITY",
            "CREATE POLICY tenant_isolation",
        ):
            self.assertIn(fragment, source, fragment)
        # app_rw / app_ro must not gain any direct privilege on the new credential tables.
        self.assertNotIn("TO app_rw", source)
        self.assertNotIn("TO app_ro", source)


class F61bDeploymentConfigContractTests(unittest.TestCase):
    # spec: F61.ac4 — deploy config names the MFA encryption key, the reset pepper, and the
    # login-throttle tunables, and still routes DB access through the auth_svc principal.
    def test_env_example_documents_hardening_secrets_and_tunables(self) -> None:
        source = ENV_EXAMPLE.read_text(encoding="utf-8")
        for fragment in (
            "AUTH_MFA_ENC_KEY=",
            "AUTH_PASSWORD_RESET_PEPPER=",
            "AUTH_LOGIN_MAX_FAILURES=",
            "AUTH_LOGIN_LOCKOUT_SECONDS=",
            "AUTH_PASSWORD_RESET_TTL_SECONDS=",
        ):
            self.assertIn(fragment, source, fragment)
        # Reaffirm the CS-061a least-privilege principal; the hardening tables reuse auth_svc.
        self.assertIn("auth_svc", source)


if __name__ == "__main__":
    unittest.main()
