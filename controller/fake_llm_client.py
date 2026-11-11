"""Wraps the camp LLMController without editing llm_controller.py.

FakeLLMClient implements the same complete() as LLMClient so --fake-llm
works with no API key. ClusterLLMController is that same controller plus
a SCALE_DOWN cooldown for the live Kubernetes loop.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence

from controller.base import Observation
from controller.llm_client import LLMResponse
from controller.llm_controller import LLMController, ParsedAction

SCALE_DOWN_COOLDOWN_TICKS = 4


class ClusterLLMController(LLMController):
    """Camp LLMController, with a cooldown on SCALE_DOWN after SCALE_UP."""

    def reset(self) -> None:
        super().reset()
        self._last_scale_up_tick = None

    def apply_guardrails(self, parsed: ParsedAction, obs: Observation) -> ParsedAction:
        if parsed.invalid:
            return parsed
        if (
            parsed.action == "SCALE_DOWN"
            and self._last_scale_up_tick is not None
            and obs.tick - self._last_scale_up_tick < SCALE_DOWN_COOLDOWN_TICKS
        ):
            waited = obs.tick - self._last_scale_up_tick
            note = (
                f"[guardrail: SCALE_DOWN blocked, {waited} ticks since "
                f"SCALE_UP, need {SCALE_DOWN_COOLDOWN_TICKS}]"
            )
            return ParsedAction(
                action="NO_CHANGE",
                amount=0,
                reason=f"{parsed.reason} {note}".strip(),
                invalid=False,
            )
        if parsed.action == "SCALE_UP":
            self._last_scale_up_tick = obs.tick
        return parsed


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
