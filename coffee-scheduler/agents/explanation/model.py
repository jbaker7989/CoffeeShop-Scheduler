"""Anthropic SDK adapter for optional per-shift summaries."""
from __future__ import annotations

import os


class AnthropicExplainer:
    def __call__(self, system: str, user: str) -> str:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            return ""

        import anthropic

        try:
            with anthropic.Anthropic(api_key=api_key, timeout=10.0, max_retries=0) as client:
                response = client.messages.create(
                    model="claude-sonnet-5-5",
                    system=system,
                    messages=[{"role": "user", "content": user}],
                    thinking={"type": "adaptive"},
                    output_config={"effort": "low"},
                    max_tokens=1024,
                )
        except anthropic.APIError:
            return ""

        if response.stop_reason != "end_turn":
            return ""
        return "\n".join(block.text for block in response.content if block.type == "text")
