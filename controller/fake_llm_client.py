"""Fake LLM client for tests and the simulator's --fake-llm flag.

Same complete() shape as LLMClient, so no API key is required.
The cluster smoke test is the llm_fake plugin, not this file.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence

from controller.llm_client import LLMResponse


class FakeLLMClient:
    """Canned or heuristic JSON. No API key. Not a baseline — ignores operator notes."""

    provider_name = "fake"
    model = "fake"

    def __init__(self, replies: str | Sequence[str] | None = None, fail: bool = False):
        self.replies = replies
        self.fail = fail
        self.index = 0
        self.systems: list[str] = []
        self.prompts: list[str] = []
        self.call_log: list[dict] = []

    def complete(self, system: str, prompt: str, use_cache: bool = True) -> LLMResponse | None:
        self.systems.append(system)
        self.prompts.append(prompt)
        if self.fail:
            self.call_log.append(
                {"latency_s": None, "cached": False, "ok": False, "error": "fake failure"}
            )
            return None
        if isinstance(self.replies, str):
            text = self.replies
        elif isinstance(self.replies, Sequence):
            text = self.replies[min(self.index, len(self.replies) - 1)]
            self.index += 1
        else:
            cpu = _grab(r"CPU:\s*([\d.]+)%", prompt, 50)
            lat = _grab(r"Latency:\s*([\d.]+)\s*ms", prompt, 100)
            backlog = _grab(r"Backlog:\s*([\d.]+)", prompt, 0)
            if lat > 300 or cpu >= 85 or backlog > 0:
                action, amount, reason = "SCALE_UP", 2, "high load"
            elif cpu <= 30:
                action, amount, reason = "SCALE_DOWN", 1, "idle"
            else:
                action, amount, reason = "NO_CHANGE", 0, "steady"
            text = json.dumps({"action": action, "amount": amount, "reason": reason})
        self.call_log.append({"latency_s": 0.0, "cached": False, "ok": True, "error": ""})
        return LLMResponse(text, latency_s=0.0, cached=False, model=self.model)


def _grab(pattern: str, text: str, default: float) -> float:
    match = re.search(pattern, text)
    return float(match.group(1)) if match else default
