"""Human controller: YOU are the autoscaler (warm-up game).

Each tick you see the same Observation every other controller sees, and type:

    +N     add N replicas      (e.g. +3)
    -N     remove N replicas   (e.g. -1)
    Enter  no change
    q      quit (the run is not saved)

Your results are saved in the same format as every other controller, so you
show up on the leaderboard next to HPA and the LLM controllers.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from controller.base import Controller, Decision, Observation

_MOVE = re.compile(r"^([+-])\s*(\d+)$")


class QuitGame(Exception):
    """Raised when the player types 'q'."""


def parse_move(raw: str) -> int | None:
    """Turn what the player typed into a replica change.

    Returns the change (0 for Enter), or None if the input isn't valid.
    Raises QuitGame for 'q'.
    """
    text = raw.strip().lower()
    if text == "":
        return 0
    if text in ("q", "quit", "exit"):
        raise QuitGame
    match = _MOVE.match(text)
    if not match:
        return None
    sign, amount = match.groups()
    return int(amount) if sign == "+" else -int(amount)


class HumanController(Controller):
    def __init__(
        self,
        name: str = "human",
        sla_latency_ms: float = 300.0,
        input_fn: Callable[[str], str] = input,
        print_fn: Callable[[str], None] = print,
    ):
        self.name = name
        self.sla_latency_ms = sla_latency_ms
        self.input_fn = input_fn
        self.print_fn = print_fn

    def decide(self, obs: Observation) -> Decision:
        self._show(obs)
        while True:
            raw = self.input_fn(
                "  your move [+N / -N / Enter = no change / q = quit]: "
            )
            change = parse_move(raw)
            if change is not None:
                break
            self.print_fn("  ?? type something like +2, -1, or just press Enter")

        target = obs.replicas_total + change
        move = f"{change:+d}" if change else "no change"
        return Decision(target_replicas=target, info={"reason": f"human: {move}"})

    def _show(self, obs: Observation) -> None:
        p = self.print_fn
        minutes, seconds = divmod(int(obs.time_s), 60)
        status = (
            "OK" if obs.latency_ms <= self.sla_latency_ms else "SLOW (SLA violated)"
        )

        p("")
        p(f"--- tick {obs.tick}  (t = {minutes}:{seconds:02d}) " + "-" * 40)
        p(
            f"  load      {obs.request_rate:7.0f} req/s     cpu      {obs.cpu_pct:5.0f} %"
        )
        p(f"  latency   {obs.latency_ms:7.0f} ms        {status}")
        p(
            f"  replicas  {obs.replicas_ready:3d} ready + {obs.replicas_pending} starting   "
            f"backlog {obs.backlog:,.0f} requests"
        )
        if obs.history:
            recent = " -> ".join(f"{h['request_rate']:.0f}" for h in obs.history)
            p(f"  recent load (req/s): {recent} -> {obs.request_rate:.0f}")
        for note in obs.operator_notes:
            p("")
            p("  >>> OPERATOR NOTE: " + note)
