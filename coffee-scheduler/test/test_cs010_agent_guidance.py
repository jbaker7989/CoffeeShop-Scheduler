"""Offline documentation contracts for CS-010.

spec: F6.ac1–ac5 — project and agent guidance stays complete, actionable, linked, and safe.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENT_DOCS = (
    "agents/scheduler/AGENTS.md",
    "agents/labor_compliance/AGENTS.md",
    "agents/demand_forecast/AGENTS.md",
    "agents/staff_comms/AGENTS.md",
)
SKILL_DOCS = (
    "skills/labor-rule-evaluation/SKILL.md",
    "skills/synthetic-roster-generation/SKILL.md",
    "skills/sales-weather-join/SKILL.md",
    "skills/standards-calibration/SKILL.md",
    "skills/agent-card-authoring/SKILL.md",
)


def read_doc(relative_path: str) -> str:
    path = ROOT / relative_path
    if not path.is_file():
        raise AssertionError(f"required CS-010 guidance is missing: {relative_path}")
    content = path.read_text(encoding="utf-8")
    if len(content.strip()) < 300:
        raise AssertionError(f"CS-010 guidance is still a stub: {relative_path}")
    return content


class Cs010AgentGuidanceTests(unittest.TestCase):
    # spec: F6.ac1 — root guidance matches the actual project stack, data policy and verification commands.
    def test_root_guidance_points_to_canonical_project_policy(self) -> None:
        content = read_doc("AGENTS.md")
        for required in (
            "n8n",
            "LangGraph",
            "A2A",
            "data/README.md",
            "python3 -m unittest discover -s test",
            "ruff",
            "environment-gated",
            "fail-closed",
        ):
            with self.subTest(required=required):
                self.assertIn(required.casefold(), content.casefold())

    # spec: F6.ac2 — every runtime agent documents ownership, boundaries, interfaces and shared procedures.
    def test_each_agent_guidance_has_role_and_interface_boundaries(self) -> None:
        for relative_path in AGENT_DOCS:
            with self.subTest(path=relative_path):
                content = read_doc(relative_path).casefold()
                for required in ("## owns", "## does not own", "## inputs", "## outputs", "skills/"):
                    self.assertIn(required, content)

    # spec: F6.ac3 — each shared skill gives executable steps, validation and failure handling.
    def test_each_shared_skill_has_actionable_procedure_and_checks(self) -> None:
        for relative_path in SKILL_DOCS:
            with self.subTest(path=relative_path):
                content = read_doc(relative_path).casefold()
                for required in ("## procedure", "## validation", "## failure"):
                    self.assertIn(required, content)

    # spec: F6.ac4 — required inventory exists and every local inline Markdown link resolves.
    def test_required_inventory_and_local_inline_links_resolve(self) -> None:
        paths = ("AGENTS.md", *AGENT_DOCS, *SKILL_DOCS)
        for relative_path in paths:
            content = read_doc(relative_path)
            source = ROOT / relative_path
            for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", content):
                if target.startswith(("http://", "https://", "mailto:")) or target.startswith("#"):
                    continue
                local_target = target.split("#", maxsplit=1)[0]
                if not local_target:
                    continue
                self.assertTrue(
                    (source.parent / local_target).resolve().exists(),
                    f"broken local link in {relative_path}: {target}",
                )

    # spec: F6.ac5 — root conventions protect secrets and keep synthetic fixtures distinct from customer truth.
    def test_root_guidance_protects_secrets_and_fixture_boundaries(self) -> None:
        content = read_doc("AGENTS.md").casefold()
        for required in ("never commit", "secret", "real pii", "synthetic", "customer"):
            with self.subTest(required=required):
                self.assertIn(required, content)


if __name__ == "__main__":
    unittest.main()
