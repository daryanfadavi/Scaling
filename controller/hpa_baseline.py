"""HPA baseline: Kubernetes' Horizontal Pod Autoscaler replica formula.

>>> STUB -- Pair A implements this at Camp QMIND. <<<
Definition of done: `pytest -m todo tests/todo/test_hpa.py` passes, and
`python -m experiments.run --controller hpa --scenario spike` runs.

This is our primary baseline, so it should behave like the real HPA as closely
as our simple simulator allows (docs: kubernetes.io/docs/tasks/run-application/
horizontal-pod-autoscale/). It ignores operator notes entirely -- real HPA
can't read them.

THE FORMULA
-----------
    desired = ceil(current_replicas * current_cpu / target_cpu)

with target_cpu = 60 (%). Use obs.replicas_ready as current_replicas -- those
are the replicas whose CPU we're measuring.

Worked example: 3 replicas at 90% CPU with a 60% target:
    ratio   = 90 / 60 = 1.5
    desired = ceil(3 * 1.5) = ceil(4.5) = 5

Another: 4 replicas at 45% CPU:
    ratio   = 45 / 60 = 0.75
    desired = ceil(4 * 0.75) = 3

TOLERANCE (10%)
---------------
If the ratio current_cpu / target_cpu is within 0.9..1.1, HPA does nothing
(keeps the current replica count). This stops it from flapping on tiny noise.
    4 replicas at 63%: ratio 1.05 -> inside tolerance -> stay at 4

SCALE-DOWN STABILIZATION (5 minutes = 20 ticks)
-----------------------------------------------
HPA scales UP immediately, but scales DOWN cautiously: it remembers its raw
recommendation (the formula result) for each of the last 20 ticks, including
this one, and uses the MAXIMUM of them. So after a spike, it keeps the high
replica count for 5 minutes before shrinking. Example:
    tick 0: 4 replicas at 90%  -> recommendation 6 -> target 6
    tick 1: 6 replicas at 30%  -> recommendation 3, but max(6, 3) = 6 -> target 6
    ...
    tick 20: tick 0 has left the 20-tick window -> target 3

HINTS
-----
* `import math` and use math.ceil.
* Keep the recent recommendations in a list or a collections.deque(maxlen=...)
  and clear it in reset().
* Put the reason in the Decision's info, e.g. {"reason": "cpu 90% vs 60% -> 5"}.
  It ends up in the per-tick CSV, which makes debugging much easier.
* obs.cpu_pct is capped at 100, so during a huge spike HPA underestimates how
  many replicas it needs. That's realistic -- the real HPA has the same blind
  spot. Don't "fix" it.
* Don't clamp to min/max replicas or limit the step size here: the environment
  applies those guardrails equally to every controller.
"""

from __future__ import annotations

from collections import deque

from controller.base import Controller, Decision, Observation


class HPAController(Controller):
    name = "hpa"

    def __init__(
        self,
        target_cpu_pct: float = 60.0,
        tolerance: float = 0.1,
        stabilization_ticks: int = 20,
    ):
        self.target_cpu_pct = target_cpu_pct
        self.tolerance = tolerance
        self.stabilization_ticks = stabilization_ticks
        self.reset()

    def reset(self) -> None:
        # TODO(pair A): set up whatever state you need for the stabilization window.
        self.recent_recommendations = deque(maxlen=self.stabilization_ticks)

    def decide(self, obs: Observation) -> Decision:
        # TODO(pair A):
        #   1. ratio = obs.cpu_pct / self.target_cpu_pct
        #   2. if the ratio is within the tolerance band -> recommendation = current replicas
        #      else -> recommendation = ceil(current * ratio)
        #   3. remember the recommendation; target = max over the last
        #      `stabilization_ticks` recommendations
        #   4. return Decision(target_replicas=target, info={"reason": ...})
        raise NotImplementedError(
            "HPAController.decide is a stub -- see the docstring in controller/hpa_baseline.py"
        )
