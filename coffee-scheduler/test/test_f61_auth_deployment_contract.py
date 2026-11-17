"""CS-061a migration and deploy contracts that require no local database."""
from __future__ import annotations

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
VERSIONS = ROOT / "coffee-scheduler" / "db" / "migrations" / "versions"
ENV_EXAMPLE = ROOT / ".env.example"


def migration_metadata(path: Path) -> tuple[str, str | None]:
    values: dict[str, str | None] = {}
    for statement in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1 and isinstance(statement.targets[0], ast.Name):
            if statement.targets[0].id in {"revision", "down_revision"}:
                values[statement.targets[0].id] = ast.literal_eval(statement.value)
    return values["revision"], values["down_revision"]


class F61MigrationAndDeploymentContractTests(unittest.TestCase):
    # spec: F61.ac1 — auth extends F60's completed migration chain without a fork or revision collision.
    def test_auth_is_single_linear_head_after_f60(self) -> None:
        auth = VERSIONS / "0010_cs061a_auth.py"
        self.assertTrue(auth.is_file(), "CS-061a needs 0010_cs061a_auth.py")
        self.assertEqual(("0010", "0009"), migration_metadata(auth))
        metadata = {path: migration_metadata(path) for path in VERSIONS.glob("*.py")}
        revisions = [revision for revision, _ in metadata.values()]
        self.assertEqual(len(revisions), len(set(revisions)), "Alembic revision identifiers must be unique")
        parents = {parent for _, parent in metadata.values() if parent is not None}
        heads = set(revisions) - parents
        self.assertEqual(1, len(heads), "Alembic must have exactly one linear migration head")
        self.assertIn("0012", heads, "CS-061c must extend, not fork, the CS-061a chain")
        self.assertIn("0010", parents, "CS-061a migration must remain an ancestor of later auth migrations")

    # spec: F61.ac1 — deployment uses a separate login principal; routine RLS roles and a
    # superuser DSN must never receive credential lookup/refresh authority.
    def test_deploy_configuration_requires_separate_auth_login_principal(self) -> None:
        source = ENV_EXAMPLE.read_text(encoding="utf-8")
        for fragment in (
            "AUTH_DATABASE_URL=postgresql://AUTH_LOGIN_USER:AUTH_LOGIN_PASSWORD@",
            "auth_svc remains a NOLOGIN least-privilege role",
            "separately provisioned LOGIN principal",
            "GRANT auth_svc TO",
            "AUTH_DATABASE_URL must never use app_rw, app_ro,",
            "or the coffeeshop superuser URL",
            "AUTH_JWT_PRIVATE_KEY_PEM=",
            "AUTH_JWT_PUBLIC_KEYS_PEM=",
            "AUTH_JWT_ACTIVE_KID=",
            "AUTH_JWT_ISSUER=",
            "AUTH_JWT_AUDIENCE=",
            "AUTH_REFRESH_PEPPER=",
        ):
            self.assertIn(fragment, source)
        self.assertNotIn("logs in as the auth_svc role", source)


if __name__ == "__main__":
    unittest.main()
