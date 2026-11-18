"""Deterministic checks applied to every replica number, LLM or fallback."""

from __future__ import annotations

from controllers.llm.config import ControllerConfig
from controllers.llm.models import ClusterObservation
from controllers.llm.state import StateManager


def is_emergency(obs: ClusterObservation, config: ControllerConfig) -> bool:
    if obs.cpu_utilization_percent is not None and obs.cpu_utilization_percent >= config.emergency_cpu_percent:
        return True
    if obs.p95_latency_ms is not None and obs.p95_latency_ms >= config.emergency_p95_ms:
        return True
    if obs.error_rate is not None and obs.error_rate >= config.emergency_error_rate:
        return True
    return False


def is_low_demand(obs: ClusterObservation, config: ControllerConfig) -> bool:
    """True only when CPU is present and clearly low. Missing CPU is not low demand."""
    if obs.cpu_utilization_percent is None:
        return False
    if obs.cpu_utilization_percent > config.low_cpu_percent:
        return False
    if obs.p95_latency_ms is not None and obs.p95_latency_ms >= config.emergency_p95_ms:
        return False
    if obs.error_rate is not None and obs.error_rate >= config.emergency_error_rate:
        return False
    return True


def validate(
    proposed: int,
    obs: ClusterObservation,
    state: StateManager,
    config: ControllerConfig,
    now: float,
) -> tuple[int, list[str]]:
    """Return the replica count we are willing to recommend, plus why it changed."""
    notes: list[str] = []
    current = obs.desired_replicas
    if current is None:
        notes.append("desired replicas unknown; holding")
        return config.min_replicas, notes

    target = proposed
    if target < config.min_replicas:
        notes.append(f"raised {target}->{config.min_replicas} (min)")
        target = config.min_replicas
    if target > config.max_replicas:
        notes.append(f"lowered {target}->{config.max_replicas} (max)")
        target = config.max_replicas

    if obs.stale:
        notes.append("stale snapshot; holding")
        return current, notes

    emergency = is_emergency(obs, config)
    low = is_low_demand(obs, config)
    state.mark_demand(now, low and not emergency)

    if target > current:
        step = target - current
        if step > config.max_scale_up_step:
            notes.append(f"scale-up step {step} capped at +{config.max_scale_up_step}")
            target = current + config.max_scale_up_step
        starting = obs.replicas_starting or 0
        if config.startup_awareness and starting > 0 and not emergency:
            notes.append(f"{starting} replicas not ready yet; not adding more")
            target = current
        elif config.startup_awareness and starting > 0 and emergency:
            notes.append("not-ready replicas present, but overload allows another step")
    elif target < current:
        if obs.cpu_utilization_percent is None and obs.requests_per_second is None:
            notes.append("missing cpu and request rate; no scale-down")
            return current, notes
        waited = state.low_demand_seconds(now)
        if waited < config.scale_down_stabilization_seconds:
            notes.append(
                f"scale-down held; low demand for {waited:.0f}s, "
                f"need {config.scale_down_stabilization_seconds:.0f}s"
            )
            return current, notes
        since = state.seconds_since_scale_down(now)
        if since is not None and since < config.minimum_seconds_between_scale_downs:
            notes.append("scale-down cooldown")
            return current, notes
        step = current - target
        if step > config.max_scale_down_step:
            notes.append(f"scale-down step {step} capped at -{config.max_scale_down_step}")
            target = current - config.max_scale_down_step

    return target, notes
