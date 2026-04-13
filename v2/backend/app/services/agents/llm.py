"""Thin Anthropic wrapper for the agent pipeline.

Centralises model name, JSON parsing, and error handling so every agent
node has a single `await ask_llm(...)` call.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Model ID for the agent pipeline.
# Sonnet is the speed/quality sweet spot for 4 agents × ~40 tickers.
AGENT_MODEL = "claude-sonnet-4-6"


class LLMClient:
    """Stateless async wrapper around anthropic.Anthropic."""

    def __init__(self, api_key: str, model: str = AGENT_MODEL):
        self.api_key = api_key
        self.model = model
        self._client = None

    def _ensure_client(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic(api_key=self.api_key)
        return self._client

    async def ask_json(
        self,
        system: str,
        user: str,
        max_tokens: int = 1024,
    ) -> dict[str, Any]:
        """Run a single prompt, parse JSON reply, return {} on failure."""
        if not self.api_key:
            return {}

        def _call() -> str:
            client = self._ensure_client()
            msg = client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user}],
            )
            return msg.content[0].text if msg.content else ""

        try:
            loop = asyncio.get_event_loop()
            text = await loop.run_in_executor(None, _call)
        except Exception as exc:
            logger.warning("LLM call failed: %s", exc)
            return {}

        return _extract_json(text) or {}


def _extract_json(text: str) -> Optional[dict[str, Any]]:
    if not text:
        return None
    stripped = text.strip()
    # Direct parse
    try:
        return json.loads(stripped)
    except Exception:
        pass
    # Code fence
    m = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", stripped)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    # First {...} block
    m = re.search(r"\{[\s\S]+\}", stripped)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    return None


def clamp(v: Any, lo: float, hi: float, default: float = 0.0) -> float:
    try:
        f = float(v)
        if f != f:
            return default
        return max(lo, min(hi, f))
    except (TypeError, ValueError):
        return default
