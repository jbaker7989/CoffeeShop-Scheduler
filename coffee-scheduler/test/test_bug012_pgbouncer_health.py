"""BUG-012 regression coverage for the PgBouncer healthcheck.

Run with: python3 -m unittest coffee-scheduler/test/test_bug012_pgbouncer_health.py

The lifecycle test has its own Compose project, removes published ports, and tears down
its containers and volume even when an assertion fails.  It therefore cannot attach to
or stop a developer's normal ``docker compose up`` stack.
"""
import json
import shutil
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = ROOT / "docker-compose.yml"
PGBOUNCER_IMAGE = "edoburu/pgbouncer:v1.23.1-p2"
ISOLATED_OVERRIDE = """\
services:
  postgres:
    # The base file assigns a fixed name and host port; neither is safe in a test.
    container_name: !reset null
    ports: !reset []
  pgbouncer:
    ports: !reset []
"""


def command(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, text=True, capture_output=True, check=check)


@unittest.skipUnless(shutil.which("docker"), "Docker CLI required for BUG-012 Compose regression")
class PgBouncerHealthcheckRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        available = command("docker", "info", check=False)
        if available.returncode:
            raise unittest.SkipTest(f"Docker daemon unavailable: {available.stderr.strip()}")

    # spec: BUG-012 — the rendered healthcheck uses a real executable from the pinned image.
    def test_rendered_healthcheck_uses_pinned_image_executable_on_loopback(self) -> None:
        rendered = command(
            "docker", "compose", "-f", str(COMPOSE_FILE), "config", "--format", "json"
        )
        service = json.loads(rendered.stdout)["services"]["pgbouncer"]
        self.assertEqual(PGBOUNCER_IMAGE, service["image"])
        self.assertEqual(
            ["CMD-SHELL", "pg_isready -h 127.0.0.1 -p 6432 -U coffeeshop"],
            service["healthcheck"]["test"],
        )
        self.assertNotIn("bash", service["healthcheck"]["test"][1])

        executable = command(
            "docker", "run", "--rm", "--entrypoint", "sh", PGBOUNCER_IMAGE,
            "-ec", "command -v pg_isready",
        )
        self.assertTrue(executable.stdout.strip().endswith("pg_isready"), executable.stdout)

    # spec: BUG-012 — PgBouncer becomes healthy and accepts authenticated loopback clients on 6432.
    def test_isolated_stack_becomes_healthy_authenticates_and_rejects_closed_port(self) -> None:
        project = f"bug012-regression-{uuid.uuid4().hex[:10]}"
        with tempfile.TemporaryDirectory(prefix="bug012-compose-") as tempdir:
            override = Path(tempdir) / "isolated.yml"
            override.write_text(ISOLATED_OVERRIDE)
            compose = (
                "docker", "compose", "-p", project, "-f", str(COMPOSE_FILE), "-f", str(override)
            )
            try:
                started = command(*compose, "up", "-d", "--wait", "--wait-timeout", "90", check=False)
                self.assertEqual(0, started.returncode, f"compose up failed:\n{started.stdout}{started.stderr}")

                container = command(*compose, "ps", "-q", "pgbouncer").stdout.strip()
                self.assertTrue(container, "Compose did not create a PgBouncer container")
                health = command("docker", "inspect", "--format", "{{.State.Health.Status}}", container)
                self.assertEqual("healthy", health.stdout.strip())

                authenticated = command(
                    *compose, "exec", "-T", "-e", "PGPASSWORD=coffeeshop", "pgbouncer",
                    "psql", "-h", "127.0.0.1", "-p", "6432", "-U", "coffeeshop", "-d", "coffeeshop",
                    "-v", "ON_ERROR_STOP=1", "-tAc", "SELECT 1",
                )
                self.assertEqual("1", authenticated.stdout.strip())

                closed_port = command(
                    *compose, "exec", "-T", "pgbouncer", "pg_isready", "-h", "127.0.0.1",
                    "-p", "6433", "-U", "coffeeshop", check=False,
                )
                self.assertNotEqual(0, closed_port.returncode, "closed port 6433 unexpectedly accepted")
            finally:
                command(*compose, "down", "--volumes", "--remove-orphans", check=False)


if __name__ == "__main__":
    unittest.main()
