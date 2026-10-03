"""The simulated cluster: replicas, traffic, backlog, latency.

How one episode runs (see `run_episode` at the bottom):

    obs = env.reset()                  # simulate tick 0 with the initial replicas
    while obs is not None:
        decision = controller.decide(obs)
        obs = env.step(decision)       # apply decision, simulate the next tick

Timing, precisely: the controller sees the result of tick t and picks a
target. That target takes effect from tick t+1:

  * Scale-DOWN is immediate: the replicas are gone for tick t+1.
  * Scale-UP is delayed: new replicas are "pending" during ticks t+1 and t+2
    and start serving traffic at tick t+3 (startup_delay_ticks = 2 full ticks,
    i.e. 30 simulated seconds of waiting for the pod to boot).

WHY THE STARTUP DELAY MATTERS (please don't remove it):
In real clusters a new pod takes time to schedule, pull its image and warm up.
That delay is the entire reason preemptive scaling is valuable. If new replicas
were ready instantly, a purely reactive controller (like HPA) could wait until
the spike arrives and scale up with no penalty -- knowing about the spike in
advance (the operator note) would be worthless, and the LLM + context
controller could never beat HPA. With the delay, a reactive controller always
suffers a few overloaded ticks after a sudden spike, while a controller that
scales up *before* the spike can avoid them. That gap is exactly what our
experiment measures.

Everything random (load noise, latency noise) is drawn up front in `reset()`
from the seed, so every controller faces the identical trace.
"""

from __future__ import annotations

import logging
from collections import deque
from typing import Any

import numpy as np

from controller.base import Controller, Decision, Observation
from simulator.config import SimConfig
from simulator.latency import latency_ms
from simulator.scenario import Scenario
from simulator.workloads import noisy_trace

log = logging.getLogger(__name__)


def apply_env_guardrails(
    requested: int, current_total: int, cfg: SimConfig
) -> tuple[int, str]:
    """Clamp a requested target the same way for every controller.

    1. Keep it inside [min_replicas, max_replicas].
    2. Change by at most max_step replicas per tick (relative to ready + pending).

    Returns (applied_target, note). `note` is "" when nothing was clamped.
    """
    target = requested
    notes = []

    if target < cfg.min_replicas:
        notes.append(f"raised {target}->{cfg.min_replicas} (min_replicas)")
        target = cfg.min_replicas
    if target > cfg.max_replicas:
        notes.append(f"lowered {target}->{cfg.max_replicas} (max_replicas)")
        target = cfg.max_replicas

    change = target - current_total
    if change > cfg.max_step:
        notes.append(f"step +{change} capped at +{cfg.max_step}")
        target = current_total + cfg.max_step
    elif change < -cfg.max_step:
        notes.append(f"step {change} capped at -{cfg.max_step}")
        target = current_total - cfg.max_step

    return target, "; ".join(notes)


class ScalingEnv:
    def __init__(
        self, scenario: Scenario, seed: int = 0, config: SimConfig | None = None
    ):
        self.scenario = scenario
        self.seed = seed
        self.config = (config or SimConfig()).with_overrides(scenario.env)
        self.ticks = scenario.ticks

    # ------------------------------------------------------------------ setup

    def reset(self) -> Observation:
        cfg = self.config
        rng = np.random.default_rng(self.seed)
        # Draw ALL randomness now, in a fixed order, so it can't depend on the controller.
        self.load_trace = noisy_trace(
            self.scenario.load, self.ticks, cfg.load_noise_pct, rng
        )
        self.latency_noise = rng.normal(0.0, cfg.latency_noise_std, size=self.ticks)

        self.tick = 0
        self.ready = cfg.initial_replicas
        self.pending_ready_at: list[int] = (
            []
        )  # one entry per pending replica: the tick it becomes ready
        self.backlog = 0.0
        self.history: deque[dict[str, Any]] = deque(maxlen=cfg.history_length)
        self.records: list[dict[str, Any]] = []

        self._simulate_tick()
        return self._observe()

    # --------------------------------------------------------------- one tick

    def _simulate_tick(self) -> None:
        """Serve this tick's traffic with the replicas that are ready now."""
        cfg = self.config
        t = self.tick

        # Pending replicas whose startup delay is over join the ready pool.
        newly_ready = sum(1 for ready_at in self.pending_ready_at if ready_at <= t)
        self.pending_ready_at = [r for r in self.pending_ready_at if r > t]
        self.ready += newly_ready

        load = float(self.load_trace[t])  # req/s arriving this tick
        capacity = self.ready * cfg.capacity_per_replica  # req/s we can serve

        # Work in "requests per tick" so the backlog has clear units.
        demand = load * cfg.tick_seconds + self.backlog
        can_serve = capacity * cfg.tick_seconds
        served = min(demand, can_serve)
        self.backlog = demand - served  # unserved requests carry over to the next tick

        # CPU = fraction of capacity actually used. With no backlog this is exactly
        # 100 * load / capacity (capped at 100); with a backlog the replicas are
        # also busy draining old requests, so CPU reflects that too.
        cpu_pct = 100.0 * served / can_serve if can_serve > 0 else 100.0

        lat = latency_ms(
            load, capacity, self.backlog, cfg, noise=float(self.latency_noise[t])
        )

        self.state = {
            "tick": t,
            "time_s": t * cfg.tick_seconds,
            "load": round(load, 2),
            "capacity": capacity,
            "replicas_ready": self.ready,
            "replicas_pending": len(self.pending_ready_at),
            "cpu_pct": round(cpu_pct, 2),
            "latency_ms": round(lat, 2),
            "backlog": round(self.backlog, 2),
            "sla_violated": lat > cfg.sla_latency_ms,
        }

    def _active_notes(self) -> list[str]:
        cfg = self.config
        return [
            f"(posted at t={n.start_tick * cfg.tick_seconds:.0f}s) {n.text}"
            for n in self.scenario.operator_notes
            if n.is_active(self.tick)
        ]

    def _observe(self) -> Observation:
        s = self.state
        return Observation(
            tick=s["tick"],
            time_s=s["time_s"],
            replicas_ready=s["replicas_ready"],
            replicas_pending=s["replicas_pending"],
            cpu_pct=round(s["cpu_pct"], 1),
            request_rate=round(s["load"], 1),
            latency_ms=round(s["latency_ms"], 1),
            backlog=round(s["backlog"], 1),
            min_replicas=self.config.min_replicas,
            max_replicas=self.config.max_replicas,
            operator_notes=self._active_notes(),
            history=list(self.history),
        )

    def step(self, decision: Decision) -> Observation | None:
        """Apply the controller's decision, then simulate the next tick.

        Returns the next Observation, or None when the episode is over.
        """
        cfg = self.config
        current_total = self.ready + len(self.pending_ready_at)

        try:
            requested = round(float(decision.target_replicas))
        except (TypeError, ValueError):
            raise ValueError(
                f"target_replicas must be a number, got {decision.target_replicas!r}"
            ) from None

        applied, clamp_note = apply_env_guardrails(requested, current_total, cfg)
        if clamp_note:
            log.info("tick %d: clamped target: %s", self.tick, clamp_note)

        if applied > current_total:
            # Scale up: new replicas are pending for startup_delay_ticks full ticks.
            ready_at = self.tick + 1 + cfg.startup_delay_ticks
            self.pending_ready_at += [ready_at] * (applied - current_total)
        elif applied < current_total:
            # Scale down (immediate): cancel pending replicas first (newest first),
            # then remove ready ones.
            to_remove = current_total - applied
            self.pending_ready_at.sort()
            while to_remove and self.pending_ready_at:
                self.pending_ready_at.pop()
                to_remove -= 1
            self.ready -= to_remove

        info = decision.info or {}
        self.records.append(
            {
                **self.state,
                "operator_notes": " | ".join(self._active_notes()),
                "requested_target": requested,
                "target": applied,
                "clamp_note": clamp_note,
                "scaled": applied != current_total,
                "reason": info.get("reason", ""),
                "invalid": bool(info.get("invalid", False)),
                "llm_called": bool(info.get("llm_called", False)),
                "llm_latency_s": info.get("llm_latency_s"),
                "llm_raw": info.get("llm_raw", ""),
            }
        )
        self.history.append(
            {
                "tick": self.state["tick"],
                "replicas_ready": self.state["replicas_ready"],
                "replicas_pending": self.state["replicas_pending"],
                "cpu_pct": round(self.state["cpu_pct"], 1),
                "request_rate": round(self.state["load"], 1),
                "latency_ms": round(self.state["latency_ms"], 1),
                "backlog": round(self.state["backlog"], 1),
                "target": requested,
                "applied_target": applied,
            }
        )

        self.tick += 1
        if self.tick >= self.ticks:
            return None
        self._simulate_tick()
        return self._observe()


def run_episode(env: ScalingEnv, controller: Controller) -> list[dict[str, Any]]:
    """Run one full scenario with one controller. Returns the per-tick records."""
    controller.reset()
    obs = env.reset()
    while obs is not None:
        decision = controller.decide(obs)
        obs = env.step(decision)
    return env.records
