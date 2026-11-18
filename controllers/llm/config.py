"""Controller settings from the environment (the runner already passes SCALE_*).

There is no separate YAML loader for plugins. The block in the README is the
same settings, as environment variables.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

MODES = (
    "llm",
    "llm_context",
    "llm_history",
    "llm_predictive",
    "llm_predictive_context",
)

# Baseline modes must not receive trends, forecasts, or outcome feedback.
BASELINE_MODES = ("llm", "llm_context")


@dataclass(frozen=True)
class ControllerConfig:
    mode: str
    deployment: str
    namespace: str
    kube_context: str
    interval_seconds: float
    history_enabled: bool
    history_window_seconds: float
    history_max_samples: int
    minimum_samples_for_trend: int
    prediction_enabled: bool
    horizon_seconds: float
    max_forecast_rps: float
    startup_awareness: bool
    fallback_startup_seconds: float
    min_replicas: int
    max_replicas: int
    max_scale_up_step: int
    max_scale_down_step: int
    scale_down_stabilization_seconds: float
    minimum_seconds_between_scale_downs: float
    stale_after_seconds: float
    emergency_cpu_percent: float
    emergency_p95_ms: float
    emergency_error_rate: float
    low_cpu_percent: float
    llm_timeout_seconds: float
    llm_max_retries: int
    fake_llm: bool
    dry_run: bool
    log_decisions: bool
    operator_notes: tuple[str, ...]
    operator_event: dict | None

    @property
    def context_enabled(self) -> bool:
        return self.mode in ("llm_context", "llm_predictive_context")

    @property
    def history_in_prompt(self) -> bool:
        return self.mode not in BASELINE_MODES and self.history_enabled

    @property
    def forecast_in_prompt(self) -> bool:
        return self.mode in ("llm_predictive", "llm_predictive_context") and self.prediction_enabled

    @property
    def baseline_prompt(self) -> bool:
        return self.mode in BASELINE_MODES


def _int(name: str, default: int, *, minimum: int | None = None) -> int:
    raw = os.getenv(name, "").strip()
    value = default if not raw else int(raw)
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be >= {minimum}, got {value}")
    return value


def _float(name: str, default: float, *, minimum: float | None = None) -> float:
    raw = os.getenv(name, "").strip()
    value = default if not raw else float(raw)
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be >= {minimum}, got {value}")
    return value


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    raise ValueError(f"{name} must be true/false, got {raw!r}")


def load_config() -> ControllerConfig:
    mode = os.getenv("SCALE_LLM_MODE", "llm").strip() or "llm"
    if mode not in MODES:
        raise ValueError(f"SCALE_LLM_MODE must be one of {MODES}, got {mode!r}")
    minimum = _int("SCALE_MIN_REPLICAS", 1, minimum=1)
    maximum = _int("SCALE_MAX_REPLICAS", 10, minimum=1)
    if minimum > maximum:
        raise ValueError(f"SCALE_MIN_REPLICAS ({minimum}) > SCALE_MAX_REPLICAS ({maximum})")
    up = _int("SCALE_LLM_MAX_SCALE_UP", 3, minimum=1)
    down = _int("SCALE_LLM_MAX_SCALE_DOWN", 1, minimum=1)
    notes_raw = os.getenv("SCALE_OPERATOR_NOTES", "")
    notes = tuple(line.strip() for line in notes_raw.splitlines() if line.strip())
    event_raw = os.getenv("SCALE_OPERATOR_EVENT_JSON", "").strip()
    event = json.loads(event_raw) if event_raw else None
    if event is not None and not isinstance(event, dict):
        raise ValueError("SCALE_OPERATOR_EVENT_JSON must be a JSON object")
    return ControllerConfig(
        mode=mode,
        deployment=os.getenv("SCALE_DEPLOYMENT", "scale-app"),
        namespace=os.getenv("SCALE_NAMESPACE", "scale"),
        kube_context=os.getenv("KUBE_CONTEXT", "scale"),
        interval_seconds=_float("SCALE_LLM_INTERVAL_SECONDS", 15.0, minimum=1.0),
        history_enabled=_bool("SCALE_LLM_HISTORY", True),
        history_window_seconds=_float("SCALE_LLM_HISTORY_WINDOW_SECONDS", 180.0, minimum=1.0),
        history_max_samples=_int("SCALE_LLM_HISTORY_MAX_SAMPLES", 120, minimum=2),
        minimum_samples_for_trend=_int("SCALE_LLM_MIN_TREND_SAMPLES", 4, minimum=2),
        prediction_enabled=_bool("SCALE_LLM_PREDICTION", True),
        horizon_seconds=_float("SCALE_LLM_HORIZON_SECONDS", 60.0, minimum=1.0),
        max_forecast_rps=_float("SCALE_LLM_MAX_FORECAST_RPS", 10000.0, minimum=1.0),
        startup_awareness=_bool("SCALE_LLM_STARTUP_AWARENESS", True),
        fallback_startup_seconds=_float("SCALE_LLM_FALLBACK_STARTUP_SECONDS", 35.0, minimum=0.0),
        min_replicas=minimum,
        max_replicas=maximum,
        max_scale_up_step=up,
        max_scale_down_step=down,
        scale_down_stabilization_seconds=_float("SCALE_LLM_SCALE_DOWN_STABILIZATION_SECONDS", 120.0, minimum=0.0),
        minimum_seconds_between_scale_downs=_float("SCALE_LLM_MIN_SECONDS_BETWEEN_SCALE_DOWNS", 30.0, minimum=0.0),
        stale_after_seconds=_float("SCALE_LLM_STALE_AFTER_SECONDS", 60.0, minimum=1.0),
        emergency_cpu_percent=_float("SCALE_LLM_EMERGENCY_CPU", 95.0, minimum=0.0),
        emergency_p95_ms=_float("SCALE_LLM_EMERGENCY_P95_MS", 300.0, minimum=0.0),
        emergency_error_rate=_float("SCALE_LLM_EMERGENCY_ERROR_RATE", 0.05, minimum=0.0),
        low_cpu_percent=_float("SCALE_LLM_LOW_CPU", 40.0, minimum=0.0),
        llm_timeout_seconds=_float("SCALE_LLM_TIMEOUT_SECONDS", 8.0, minimum=0.5),
        llm_max_retries=_int("SCALE_LLM_MAX_RETRIES", 1, minimum=0),
        fake_llm=_bool("SCALE_LLM_FAKE", False),
        dry_run=_bool("SCALE_LLM_DRY_RUN", False),
        log_decisions=_bool("SCALE_LLM_LOG_DECISIONS", True),
        operator_notes=notes,
        operator_event=event,
    )
