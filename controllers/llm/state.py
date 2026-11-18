"""Rolling observations and trend summaries. Python does the math, not the LLM."""

from __future__ import annotations

from collections import deque

from controllers.llm.config import ControllerConfig
from controllers.llm.models import ClusterObservation, TrendSummary


def _trend_label(delta: float, average: float) -> str:
    # Stable when the move is under 10% of the average (or a tiny absolute CPU/RPS move).
    scale = max(abs(average), 1.0)
    if abs(delta) <= 0.10 * scale:
        return "stable"
    return "increasing" if delta > 0 else "decreasing"


def summarize(samples: list[tuple[float, float]], *, minimum: int) -> TrendSummary:
    """samples are (unix_time, value), oldest first. Unknown when too short."""
    if len(samples) < minimum:
        current = samples[-1][1] if samples else None
        return TrendSummary(current, None, None, None, None, None, None, "unknown", len(samples))
    times = [t for t, _ in samples]
    values = [v for _, v in samples]
    first_t, first_v = samples[0]
    last_t, last_v = samples[-1]
    dt = last_t - first_t
    delta = last_v - first_v
    average = sum(values) / len(values)
    change_percent = None if first_v == 0 else 100.0 * delta / abs(first_v)
    rate = None if dt <= 0 else delta / dt
    label = "unknown" if dt <= 0 else _trend_label(delta, average)
    return TrendSummary(
        current=last_v,
        moving_average=average,
        minimum=min(values),
        maximum=max(values),
        change=delta if dt > 0 else None,
        change_percent=change_percent if dt > 0 else None,
        rate_per_second=rate,
        trend=label,
        samples=len(samples),
    )


class StateManager:
    """History for one deployment. Call reset() at the start of an experiment."""

    def __init__(self, config: ControllerConfig):
        self.config = config
        self.deployment = config.deployment
        self._rows: deque[ClusterObservation] = deque()
        self._actions: deque[dict] = deque(maxlen=20)
        self._outcomes: deque[dict] = deque(maxlen=10)
        self._scale_requested_at: float | None = None
        self._scale_target: int | None = None
        self._ready_at_request: int | None = None
        self._measured_startups: deque[float] = deque(maxlen=8)
        self._low_demand_since: float | None = None
        self._last_scale_down_at: float | None = None

    def reset(self) -> None:
        self._rows.clear()
        self._actions.clear()
        self._outcomes.clear()
        self._scale_requested_at = None
        self._scale_target = None
        self._ready_at_request = None
        self._measured_startups.clear()
        self._low_demand_since = None
        self._last_scale_down_at = None

    def add(self, obs: ClusterObservation) -> str:
        """Record one observation. Returns 'ok', 'duplicate', or 'out_of_order'."""
        if self._rows and obs.unix_time < self._rows[-1].unix_time:
            return "out_of_order"
        if self._rows and obs.unix_time == self._rows[-1].unix_time:
            self._rows[-1] = obs
            return "duplicate"
        self._rows.append(obs)
        self._trim(obs.unix_time)
        if not self.config.history_enabled:
            while len(self._rows) > 1:
                self._rows.popleft()
        self._maybe_measure_startup(obs)
        return "ok"

    def _trim(self, now: float) -> None:
        cutoff = now - self.config.history_window_seconds
        while self._rows and self._rows[0].unix_time < cutoff:
            self._rows.popleft()
        while len(self._rows) > self.config.history_max_samples:
            self._rows.popleft()

    def observations(self) -> list[ClusterObservation]:
        return list(self._rows)

    def series(self, name: str) -> list[tuple[float, float]]:
        attr = {
            "cpu": "cpu_utilization_percent",
            "rps": "requests_per_second",
            "p95_latency_ms": "p95_latency_ms",
            "error_rate": "error_rate",
            "desired_replicas": "desired_replicas",
            "ready_replicas": "ready_replicas",
        }[name]
        points = []
        for obs in self._rows:
            value = getattr(obs, attr)
            if value is None:
                continue
            points.append((obs.unix_time, float(value)))
        return points

    def trends(self) -> dict[str, TrendSummary]:
        minimum = self.config.minimum_samples_for_trend
        return {name: summarize(self.series(name), minimum=minimum) for name in (
            "cpu", "rps", "p95_latency_ms", "error_rate", "desired_replicas", "ready_replicas",
        )}

    def note_scale_request(self, now: float, target: int, ready: int | None) -> None:
        self._scale_requested_at = now
        self._scale_target = target
        self._ready_at_request = ready
        self._actions.append({"unix_time": now, "requested_replicas": target, "ready_replicas": ready})

    def note_scale_down(self, now: float) -> None:
        self._last_scale_down_at = now

    def seconds_since_scale_request(self, now: float) -> float | None:
        if self._scale_requested_at is None:
            return None
        return max(0.0, now - self._scale_requested_at)

    def startup_estimate(self) -> tuple[float | None, str]:
        if self._measured_startups:
            ordered = sorted(self._measured_startups)
            mid = ordered[len(ordered) // 2]
            return mid, "measured"
        if self.config.startup_awareness:
            return self.config.fallback_startup_seconds, "assumption"
        return None, "unknown"

    def _maybe_measure_startup(self, obs: ClusterObservation) -> None:
        if self._scale_requested_at is None or self._scale_target is None:
            return
        if obs.ready_replicas is None:
            return
        if obs.ready_replicas >= self._scale_target and (
            self._ready_at_request is None or self._scale_target > self._ready_at_request
        ):
            elapsed = obs.unix_time - self._scale_requested_at
            if elapsed >= 0:
                self._measured_startups.append(elapsed)
            self._scale_requested_at = None
            self._scale_target = None

    def mark_demand(self, now: float, low: bool) -> None:
        if low:
            if self._low_demand_since is None:
                self._low_demand_since = now
        else:
            self._low_demand_since = None

    def low_demand_seconds(self, now: float) -> float:
        if self._low_demand_since is None:
            return 0.0
        return max(0.0, now - self._low_demand_since)

    def seconds_since_scale_down(self, now: float) -> float | None:
        if self._last_scale_down_at is None:
            return None
        return max(0.0, now - self._last_scale_down_at)

    def recent_actions(self) -> tuple[dict, ...]:
        return tuple(self._actions)

    def recent_outcomes(self) -> tuple[dict, ...]:
        return tuple(self._outcomes)

    def record_outcome(self, outcome: dict) -> None:
        self._outcomes.append(outcome)
