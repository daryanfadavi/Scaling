"""Normalized observations for the cluster LLM controller.

Field names follow telemetry/queries.py (docs/telemetry.md). Missing data is
None, never zero. There is no memory metric in the shared snapshot.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone


def finite_float(value: object) -> float | None:
    """A real number, or None for missing / NaN / infinity."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def finite_int(value: object) -> int | None:
    number = finite_float(value)
    if number is None:
        return None
    if not number.is_integer():
        return None
    return int(number)


@dataclass(frozen=True)
class ClusterObservation:
    """One shared-telemetry snapshot, with units made explicit.

    cpu_utilization_percent: percent of the CPU *request* (can exceed 100).
    requests_per_second: /work requests per second, all pods.
    p95_latency_ms / p50_latency_ms: server-side milliseconds.
    error_rate: fraction of /work responses that were 5xx (0 to 1).
    replicas_starting: pods that exist but are not Ready. Not a promise they
    will become Ready.
    """

    timestamp: datetime
    unix_time: float
    desired_replicas: int | None
    ready_replicas: int | None
    replicas_starting: int | None
    cpu_utilization_percent: float | None
    requests_per_second: float | None
    p50_latency_ms: float | None
    p95_latency_ms: float | None
    error_rate: float | None
    memory_utilization_percent: float | None = None  # not in the shared snapshot
    stale: bool = False

    @property
    def fresh(self) -> bool:
        return not self.stale


def observation_from_snapshot(snapshot: dict, *, now: float | None = None, stale_after_s: float = 60.0) -> ClusterObservation:
    """Map one shared telemetry snapshot dict. Does not fill missing fields."""
    unix = finite_float(snapshot.get("unix_time"))
    if unix is None:
        stamp = snapshot.get("timestamp")
        if isinstance(stamp, str):
            unix = datetime.fromisoformat(stamp).timestamp()
        else:
            unix = now if now is not None else datetime.now(timezone.utc).timestamp()
    timestamp = datetime.fromtimestamp(unix, timezone.utc)
    age = None if now is None else max(0.0, now - unix)
    return ClusterObservation(
        timestamp=timestamp,
        unix_time=unix,
        desired_replicas=finite_int(snapshot.get("replicas_desired")),
        ready_replicas=finite_int(snapshot.get("replicas_ready")),
        replicas_starting=finite_int(snapshot.get("replicas_starting")),
        cpu_utilization_percent=finite_float(snapshot.get("cpu_utilization_pct")),
        requests_per_second=finite_float(snapshot.get("request_rate_rps")),
        p50_latency_ms=finite_float(snapshot.get("latency_p50_ms")),
        p95_latency_ms=finite_float(snapshot.get("latency_p95_ms")),
        error_rate=finite_float(snapshot.get("error_rate")),
        stale=age is not None and age > stale_after_s,
    )


@dataclass(frozen=True)
class TrendSummary:
    current: float | None
    moving_average: float | None
    minimum: float | None
    maximum: float | None
    change: float | None
    change_percent: float | None
    rate_per_second: float | None
    trend: str  # increasing | stable | decreasing | unknown
    samples: int


@dataclass(frozen=True)
class Forecast:
    horizon_seconds: float
    current_rps: float | None
    forecast_rps: float | None
    method: str
    valid: bool
    samples: int
    note: str = ""


@dataclass(frozen=True)
class StartupView:
    desired_replicas: int | None
    ready_replicas: int | None
    replicas_starting: int | None
    estimated_startup_seconds: float | None
    estimate_source: str  # measured | assumption | unknown
    seconds_since_scale_request: float | None


@dataclass
class OperatorEvent:
    event_type: str
    expected_start_seconds: float | None
    expected_duration_seconds: float | None
    expected_rps: float | None
    note: str
    received_at: float
    conflict: bool = False


@dataclass(frozen=True)
class StateSnapshot:
    observation: ClusterObservation
    trends: dict[str, TrendSummary]
    forecast: Forecast
    startup: StartupView
    recent_actions: tuple[dict, ...]
    recent_outcomes: tuple[dict, ...]
    context_notes: tuple[str, ...]
    context_event: OperatorEvent | None


@dataclass
class CycleResult:
    """Raw model idea, validated target, and whether we asked Kubernetes to apply it."""

    mode: str
    source: str  # llm | fake | fallback | hold
    prompt_version: str
    model: str
    inference_latency_s: float | None
    current_desired: int | None
    raw_desired: int | None
    validated_desired: int
    action: str
    reason: str
    safety_notes: list[str] = field(default_factory=list)
    forecast: dict | None = None
    applied: bool = False
    apply_error: str = ""
    invalid_model_output: bool = False
