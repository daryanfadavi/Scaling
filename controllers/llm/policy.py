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


def demand_signal(obs: ClusterObservation, state: StateManager, config: ControllerConfig) -> str:
    """low, high, or unknown.

    CPU is required. Latency, error rate, and a rising request-rate or latency
    trend are optional vetoes: present and bad means high; missing does not.
    """
    if is_emergency(obs, config):
        return "high"
    if obs.cpu_utilization_percent is None:
        return "unknown"
    if obs.cpu_utilization_percent > config.low_cpu_percent:
        return "high"
    if obs.p95_latency_ms is not None and obs.p95_latency_ms >= config.emergency_p95_ms:
        return "high"
    if obs.error_rate is not None and obs.error_rate >= config.emergency_error_rate:
        return "high"
    trends = state.trends()
    for name in ("rps", "p95_latency_ms"):
        trend = trends.get(name)
        if (
            trend is not None
            and trend.samples >= config.minimum_samples_for_trend
            and trend.trend == "increasing"
        ):
            return "high"
    return "low"


def is_low_demand(obs: ClusterObservation, config: ControllerConfig, state: StateManager | None = None) -> bool:
    """True only when CPU is present and clearly low. Missing CPU is not low demand."""
    if state is None:
        if obs.cpu_utilization_percent is None or obs.cpu_utilization_percent > config.low_cpu_percent:
            return False
        if obs.p95_latency_ms is not None and obs.p95_latency_ms >= config.emergency_p95_ms:
            return False
        if obs.error_rate is not None and obs.error_rate >= config.emergency_error_rate:
            return False
        return True
    return demand_signal(obs, state, config) == "low"


def validate(
    proposed: int,
    obs: ClusterObservation,
    state: StateManager,
    config: ControllerConfig,
    now: float,
    baseline: int | None = None,
) -> tuple[int, list[str]]:
    """Return the replica count we are willing to recommend, plus why it changed.

    baseline is the authoritative desired count. While a scale is waiting for
    telemetry to catch up, that is the last accepted target, not the stale snapshot.
    """
    notes: list[str] = []
    current = obs.desired_replicas if baseline is None else baseline
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
    state.mark_demand(now, demand_signal(obs, state, config))
    acked = state.acknowledged_target

    if target > current:
        step = target - current
        if step > config.max_scale_up_step:
            notes.append(f"scale-up step {step} capped at +{config.max_scale_up_step}")
            target = current + config.max_scale_up_step
        if _startup_blocks(obs, state, config, now) and not emergency:
            pending = state.unready_requested(obs)
            starting = obs.replicas_starting
            if starting:
                notes.append(f"{starting} replicas not ready yet; not adding more")
            elif pending:
                notes.append(f"{pending} requested replicas are not ready yet; not adding more")
            target = current
        elif emergency and _startup_blocks(obs, state, config, now):
            notes.append("not-ready replicas present, but overload allows another step")
    elif target < current:
        if state.reconciliation_pending and acked is not None:
            notes.append("previous scale request not reconciled")
            return acked, notes
        if demand_signal(obs, state, config) != "low":
            notes.append("demand is not low; no scale-down")
            return current, notes
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

    if state.reconciliation_pending and acked is not None and target != acked:
        if target < acked or not emergency:
            notes.append("previous scale request not reconciled")
            return acked, notes
    return target, notes


def _startup_blocks(obs: ClusterObservation, state: StateManager, config: ControllerConfig, now: float) -> bool:
    """True while extra capacity is not Ready and the startup window is still open.

    A window that already elapsed does not block forever: pods that never become
    Ready must not freeze scale-up. With no prior scale request, not-ready pods
    still block, which is the first observation of a cold start.
    """
    if not config.startup_awareness:
        return False
    pending = state.unready_requested(obs) or 0
    starting = obs.replicas_starting or 0
    if pending <= 0 and starting <= 0:
        return False
    since = state.seconds_since_scale_request(now)
    startup_s, _source = state.startup_estimate()
    if since is None or startup_s is None:
        return True
    return since < startup_s
