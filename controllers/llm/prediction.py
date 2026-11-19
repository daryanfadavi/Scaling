"""Bounded linear forecast of request rate. Not an LLM guess."""

from __future__ import annotations

from controllers.llm.config import ControllerConfig
from controllers.llm.models import Forecast


def _winsorize(values: list[float]) -> list[float]:
    if len(values) < 5:
        return list(values)
    ordered = sorted(values)
    lo = ordered[max(0, len(ordered) // 10)]
    hi = ordered[min(len(ordered) - 1, (len(ordered) * 9) // 10)]
    return [min(hi, max(lo, v)) for v in values]


def forecast_rps(samples: list[tuple[float, float]], config: ControllerConfig) -> Forecast:
    horizon = config.horizon_seconds
    if not config.prediction_enabled:
        current = samples[-1][1] if samples else None
        return Forecast(horizon, current, None, "disabled", False, len(samples), "prediction disabled")
    if len(samples) < config.minimum_samples_for_trend:
        current = samples[-1][1] if samples else None
        return Forecast(horizon, current, None, "insufficient_history", False, len(samples), "not enough samples")

    times = [t for t, _ in samples]
    raw = [v for _, v in samples]
    values = _winsorize(raw)
    current = raw[-1]
    t0 = times[0]
    xs = [t - t0 for t in times]
    mean_x = sum(xs) / len(xs)
    mean_y = sum(values) / len(values)
    var_x = sum((x - mean_x) ** 2 for x in xs)
    if var_x <= 0:
        return Forecast(horizon, current, current, "flat", True, len(samples), "no time spread")
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, values)) / var_x
    future_x = xs[-1] + horizon
    raw_forecast = mean_y + slope * (future_x - mean_x)
    # One wild slope cannot more than double (or zero-out) current demand.
    cap_hi = min(config.max_forecast_rps, max(current, 1.0) * 2.0)
    cap_lo = 0.0
    forecast = min(cap_hi, max(cap_lo, raw_forecast))
    note = ""
    if forecast != raw_forecast:
        note = "clamped"
    return Forecast(horizon, current, forecast, "bounded_linear_trend", True, len(samples), note)
