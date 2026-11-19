"""Bounded rules used when the model is missing or its JSON cannot be used.

This is not Kubernetes HPA. Missing metrics are not treated as zero load.
"""

from __future__ import annotations

from controllers.llm.config import ControllerConfig
from controllers.llm.models import ClusterObservation
from controllers.llm.state import StateManager


def fallback_desired(obs: ClusterObservation, state: StateManager, config: ControllerConfig) -> tuple[int, str]:
    current = obs.desired_replicas
    if current is None:
        return config.min_replicas, "no desired count; using min replicas"
    cpu = state.trends().get("cpu")
    if cpu is None or cpu.samples < config.minimum_samples_for_trend or cpu.moving_average is None:
        return current, "not enough cpu history; holding"
    if cpu.moving_average >= config.emergency_cpu_percent and cpu.trend in ("increasing", "stable"):
        return current + 1, f"sustained cpu average {cpu.moving_average:.0f}%; scale up 1"
    if (
        cpu.moving_average <= config.low_cpu_percent
        and cpu.trend in ("decreasing", "stable")
        and obs.p95_latency_ms is not None
        and obs.p95_latency_ms < config.emergency_p95_ms
    ):
        return current - 1, f"sustained cpu average {cpu.moving_average:.0f}%; scale down 1"
    return current, "fallback hold"
