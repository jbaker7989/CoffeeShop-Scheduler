"""Configuration close-out checks for the PostgreSQL platform."""

from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEDULER_ROOT = REPO_ROOT / "coffee-scheduler"


class PlatformConfigTests(unittest.TestCase):
    # spec: F42.ac1 — Fresh requirements install includes Alembic and psycopg.
    def test_requirements_pin_migration_and_postgres_dependencies(self):
        requirements = (SCHEDULER_ROOT / "requirements.txt").read_text()

        self.assertIn("alembic==1.20.1", requirements)
        self.assertIn("psycopg[binary,pool]==3.3.9", requirements)

    # spec: F42.ac2 — Example environment distinguishes connection URLs and excludes secrets.
    def test_env_example_documents_connection_urls_and_secret_boundary(self):
        env_example = (REPO_ROOT / ".env.example").read_text()

        self.assertIn("DATABASE_URL", env_example)
        self.assertIn("DATABASE_URL_DIRECT", env_example)
        self.assertIn("never in .env", env_example.lower())


if __name__ == "__main__":
    unittest.main()
