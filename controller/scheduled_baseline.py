"""Scheduled (cron-style) baseline: pre-scale at times an operator configured.

>>> STUB -- stretch goal. <<<

Why it's interesting: the LLM + context controller reads "campaign in ~90
seconds" and (hopefully) scales up early. But an operator who knows about the
campaign could also just schedule a scale-up, with no LLM at all. If this dumb
baseline does as well as the LLM, that's an important (and publishable!)
finding. It's the "is the LLM actually needed?" control.

FAIRNESS: the schedule is given by the operator on the command line
(`--schedule "26:17,52:2"`), standing in for a human who read the same note the
LLM gets. It must NOT be read from the scenario YAML or know the scenario name.

Behaviour to implement:
  * `schedule` is a list of (tick, replicas) pairs, e.g. [(26, 17), (52, 2)]:
    "from tick 26 on, run 17 replicas; from tick 52 on, run 2".
  * Before the first scheduled tick, hold `default_replicas`.
  * Optional: combine with HPA -- use the schedule as a *floor* and let
    HPAController handle everything else: target = max(hpa_target, floor).
    That's what real "scheduled scaling + HPA" setups do.
"""

from __future__ import annotations

from controller.base import Controller, Decision, Observation


def parse_schedule(text: str) -> list[tuple[int, int]]:
    """'26:17,52:2' -> [(26, 17), (52, 2)] (sorted by tick)."""
    pairs = []
    for item in text.split(","):
        item = item.strip()
        if not item:
            continue
        tick, replicas = item.split(":")
        pairs.append((int(tick), int(replicas)))
    return sorted(pairs)


class ScheduledController(Controller):
    name = "scheduled"

    def __init__(self, schedule: list[tuple[int, int]], default_replicas: int = 2):
        self.schedule = sorted(schedule)
        self.default_replicas = default_replicas

    def decide(self, obs: Observation) -> Decision:
        # TODO(stretch): find the last (tick, replicas) entry with tick <= obs.tick
        # and return that many replicas (or default_replicas if none yet).
        raise NotImplementedError(
            "ScheduledController.decide is a stub -- see controller/scheduled_baseline.py"
        )
