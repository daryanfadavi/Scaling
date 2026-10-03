"""Static controller: always asks for the same number of replicas.

This is the reference example of the controller interface -- read it first.
It's also a useful "do nothing smart" baseline: how bad is it to never scale?
"""

from __future__ import annotations

from controller.base import Controller, Decision, Observation


class StaticController(Controller):
    def __init__(self, replicas: int = 2):
        self.replicas = replicas
        self.name = f"static{replicas}"

    def reset(self) -> None:
        # Nothing to reset: this controller has no memory.
        pass

    def decide(self, obs: Observation) -> Decision:
        return Decision(
            target_replicas=self.replicas,
            info={"reason": f"always {self.replicas} replicas"},
        )
