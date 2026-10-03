"""The controller interface: what a controller sees, and what it returns.

Every controller (static, human, HPA, LLM, ...) follows the same contract:

    controller.reset()                 # called once before each episode
    decision = controller.decide(obs)  # called once per tick

`obs` is an `Observation` -- the ONLY information a controller gets. It never
sees the scenario name, the workload config, or the results. That's on purpose
(see AGENTS.md): if a controller could peek at "this is the spike scenario",
the comparison between controllers would be meaningless.

Look at `controller/static.py` for the smallest complete example.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class Observation:
    """A snapshot of the system at the end of one tick (1 tick = 15 seconds).

    Attributes:
        tick: Tick index, starting at 0.
        time_s: Simulated seconds since the start (tick * 15).
        replicas_ready: Replicas currently serving traffic.
        replicas_pending: Replicas that were requested but are still starting up.
        cpu_pct: Average CPU utilization of the ready replicas, 0-100.
        request_rate: Incoming requests per second during this tick.
        latency_ms: Average request latency during this tick.
        backlog: Requests that could not be served yet and are queued.
        min_replicas: The cluster will never run fewer replicas than this.
        max_replicas: The cluster will never run more replicas than this.
        operator_notes: Natural-language notes from a human operator that are
            active right now (e.g. "campaign launches in ~90 seconds"). Often empty.
        history: Up to the last 5 previous ticks, oldest first. Each entry is a
            dict with the same numeric fields as above plus `target` (what the
            controller asked for that tick) and `applied_target` (what the
            environment actually applied after guardrails).
    """

    tick: int
    time_s: float
    replicas_ready: int
    replicas_pending: int
    cpu_pct: float
    request_rate: float
    latency_ms: float
    backlog: float
    min_replicas: int
    max_replicas: int
    operator_notes: list[str] = field(default_factory=list)
    history: list[dict[str, Any]] = field(default_factory=list)

    @property
    def replicas_total(self) -> int:
        """Ready + pending: the replica count the cluster is currently heading to."""
        return self.replicas_ready + self.replicas_pending

    def to_dict(self) -> dict[str, Any]:
        """Plain-dict version, handy for building prompts or logging."""
        return asdict(self)


@dataclass
class Decision:
    """What a controller returns each tick.

    Attributes:
        target_replicas: The total number of replicas the controller wants
            (ready + pending). The environment may clamp this -- see
            `simulator/env.py`.
        info: Free-form details, recorded in the per-tick CSV. Recognized keys:
            - "reason" (str): one-line explanation of the decision.
            - "invalid" (bool): True if the controller could not produce a
              valid decision (e.g. the LLM returned garbage) and fell back.
            - "llm_latency_s" (float): wall-clock seconds spent on the LLM call.
            - "llm_called" (bool): whether an LLM call was made this tick.
    """

    target_replicas: int
    info: dict[str, Any] = field(default_factory=dict)


class Controller:
    """Base class for all controllers. Subclass this and implement `decide`."""

    #: Short name used in results files and on the leaderboard.
    name: str = "base"

    def reset(self) -> None:
        """Clear any internal state before a new episode. Override if you keep state."""

    def decide(self, obs: Observation) -> Decision:
        """Look at the observation and return a Decision. Must be overridden."""
        raise NotImplementedError
