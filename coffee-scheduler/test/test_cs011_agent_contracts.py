"""Public A2A contracts and authorization boundaries."""
import json
import unittest
from pathlib import Path
from time import time
from uuid import uuid4

import jwt
from a2a.types import AgentCard
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from agents.a2a_transport import AgentError, ServiceTrust, create_agent_app, read_card
from agents.staff_comms.preview import preview

ROOT = Path(__file__).resolve().parents[1]
TENANT = uuid4()
SUBJECT = "manager-test"


def token(key, agent_id, skill, **changes):
    now = int(time())
    claims = dict(iss="test-issuer", aud=agent_id, sub=SUBJECT, tenant_id=str(TENANT),
                  scope=skill, iat=now, exp=now + 60)
    claims.update(changes)
    return jwt.encode(claims, key, algorithm="RS256")


def message(payload, *, protocol="0.3.0"):
    return {
        "jsonrpc": "2.0", "id": "request-1", "method": "message/send",
        "params": {"message": {"kind": "message", "messageId": "message-1", "role": "user",
                               "parts": [{"kind": "data", "data": payload}]},
                   "metadata": {"protocolVersion": protocol}},
    }


class AgentContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    # spec: F7.ac1 — Runtime cards parse and bind the configured endpoint.
    def test_runtime_cards(self):
        for agent in ("scheduler", "staff_comms", "demand_forecast", "labor_compliance"):
            with self.subTest(agent=agent):
                card = read_card(agent, "https://agents.example.test/a2a")
                self.assertIsInstance(card, AgentCard)
                self.assertEqual((card.name, card.protocol_version), (agent, "0.3.0"))
                self.assertEqual(len(card.skills), 1)
                self.assertEqual(card.url, "https://agents.example.test/a2a")
        with self.assertRaises(ValueError):
            read_card("scheduler", "http://not-loopback.example.test/a2a", allow_loopback=True)

    # spec: F7.ac2 — A rejected request never reaches the handler.
    def test_transport_authorization(self):
        calls = []

        async def handler(identity, data):
            calls.append((identity.tenant_id, data))
            return {"ok": True}

        trust = ServiceTrust("test-issuer", self.key.public_key())
        app = create_agent_app("staff_comms", "https://agents.example.test/a2a", trust, handler)
        client = TestClient(app)
        skill = "preview_notification"
        request = message({"version": 1})
        good = token(self.key, "staff_comms", skill)
        bad = [None, "invalid", token(self.key, "scheduler", skill),
               token(self.key, "staff_comms", "another_skill"),
               token(self.key, "staff_comms", skill, tenant_id="invalid"),
               token(self.key, "staff_comms", skill, exp=1)]
        for authorization in bad:
            headers = {} if authorization is None else {"Authorization": f"Bearer {authorization}"}
            self.assertIn(client.post("/a2a", json=request, headers=headers).status_code, (401, 403))
        headers = {"Authorization": f"Bearer {good}"}
        self.assertIn("error", client.post("/a2a", json=message({}, protocol="9.0"), headers=headers).json())
        self.assertIn("error", client.post("/a2a", json=message({"tenant_id": str(uuid4())}), headers=headers).json())
        self.assertEqual(calls, [])
        result = client.post("/a2a", json=request, headers=headers).json()
        self.assertEqual(result["result"]["parts"][0]["data"], {"ok": True})
        self.assertEqual(calls, [(TENANT, {"version": 1})])

    # spec: F7.ac3 — Placeholder agents have no endpoint or skill.
    def test_design_cards(self):
        for name in ("inventory", "prep", "loyalty"):
            data = json.loads((ROOT / "agents" / name / "agent_card.json").read_text())
            self.assertEqual(data["status"], "design-only")
            self.assertNotIn("url", data)
            self.assertEqual(data["skills"], [])
        self.assertFalse((ROOT / "agents/payroll_hcm/agent_card.json").exists())

    # spec: F7.ac5 — Preview accepts only bounded non-PII input.
    def test_preview(self):
        data = {"version": 1, "template_id": "call_out_cover", "location_id": 45,
                "starts_at": "2026-11-27T09:00:00-05:00"}
        self.assertEqual(preview(data)["template_version"], 1)
        for change in ({"recipient": "somebody"}, {"template_id": "send"}, {"starts_at": "2026-11-27"}):
            with self.subTest(change=change), self.assertRaises(AgentError):
                preview({**data, **change})


if __name__ == "__main__":
    unittest.main()
