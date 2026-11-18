"""One control cycle: snapshot -> history -> forecast -> model -> policy."""

from __future__ import annotations

import threading
from datetime import datetime, timezone

from controllers.llm.config import ControllerConfig
from controllers.llm.context import ContextManager
from controllers.llm.decision import (
    ADVANCED_SYSTEM,
    advanced_user_prompt,
    baseline_desired,
    baseline_user_prompt,
    parse_advanced,
    prompt_version,
)
from controllers.llm.fallback import fallback_desired
from controllers.llm.models import ClusterObservation, CycleResult, StartupView, StateSnapshot, observation_from_snapshot
from controllers.llm.policy import validate
from controllers.llm.prediction import forecast_rps
from controllers.llm.state import StateManager
from controller.llm_controller import SYSTEM_PROMPT


class LLMAutoscaler:
    def __init__(self, config: ControllerConfig, client, context: ContextManager | None = None):
        self.config = config
        self.client = client
        self.state = StateManager(config)
        self.context = context or ContextManager(config.operator_notes, config.operator_event)
        self._lock = threading.Lock()
        self._awaiting_outcome: dict | None = None

    def reset(self) -> None:
        with self._lock:
            self.state.reset()
            self._awaiting_outcome = None

    def decide(self, snapshot: dict, now: float | None = None) -> CycleResult:
        with self._lock:
            return self._decide(snapshot, now)

    def note_applied(self, result: CycleResult, now: float) -> None:
        """Call only after a scale request was accepted by Kubernetes."""
        with self._lock:
            ready = None
            if self.state.observations():
                ready = self.state.observations()[-1].ready_replicas
            self.state.note_scale_request(now, result.validated_desired, ready)
            if result.current_desired is not None and result.validated_desired < result.current_desired:
                self.state.note_scale_down(now)
            self._awaiting_outcome = {
                "unix_time": now,
                "requested_replicas": result.validated_desired,
                "p95_latency_ms": None,
            }
            if self.state.observations():
                self._awaiting_outcome["p95_latency_ms"] = self.state.observations()[-1].p95_latency_ms

    def _decide(self, snapshot: dict, now: float | None) -> CycleResult:
        clock = now if now is not None else datetime.now(timezone.utc).timestamp()
        obs = observation_from_snapshot(snapshot, now=clock, stale_after_s=self.config.stale_after_seconds)
        self.state.add(obs)
        self._maybe_outcome(obs, clock)

        notes, event = self.context.visible(clock, enabled=self.config.context_enabled)
        forecast = forecast_rps(self.state.series("rps"), self.config)
        startup_s, startup_source = self.state.startup_estimate()
        startup = StartupView(
            desired_replicas=obs.desired_replicas,
            ready_replicas=obs.ready_replicas,
            replicas_starting=obs.replicas_starting,
            estimated_startup_seconds=startup_s,
            estimate_source=startup_source,
            seconds_since_scale_request=self.state.seconds_since_scale_request(clock),
        )
        view = StateSnapshot(
            observation=obs,
            trends=self.state.trends() if self.config.history_in_prompt or self.config.forecast_in_prompt else {},
            forecast=forecast,
            startup=startup,
            recent_actions=self.state.recent_actions() if not self.config.baseline_prompt else (),
            recent_outcomes=self.state.recent_outcomes() if not self.config.baseline_prompt else (),
            context_notes=notes,
            context_event=event,
        )

        raw, reason, invalid, latency, model, source = self._ask(view, obs, notes)
        if raw is None:
            raw, reason = fallback_desired(obs, self.state, self.config)
            invalid = True
            source = "hold" if "hold" in reason else "fallback"

        if obs.desired_replicas is None:
            return CycleResult(
                mode=self.config.mode,
                source="hold",
                prompt_version=prompt_version(self.config),
                model=model,
                inference_latency_s=latency,
                current_desired=None,
                raw_desired=raw,
                validated_desired=self.config.min_replicas,
                action="hold",
                reason="desired replicas missing; not scaling",
                safety_notes=["desired replicas unknown"],
                forecast=_forecast_dict(forecast),
                invalid_model_output=invalid,
            )

        validated, safety = validate(raw, obs, self.state, self.config, clock)
        action = _action(obs.desired_replicas, validated)
        return CycleResult(
            mode=self.config.mode,
            source=source,
            prompt_version=prompt_version(self.config),
            model=model,
            inference_latency_s=latency,
            current_desired=obs.desired_replicas,
            raw_desired=raw,
            validated_desired=validated,
            action=action,
            reason=reason,
            safety_notes=safety,
            forecast=_forecast_dict(forecast) if not self.config.baseline_prompt else None,
            invalid_model_output=invalid,
        )

    def _ask(self, view: StateSnapshot, obs: ClusterObservation, notes: tuple[str, ...]):
        cfg = self.config
        if cfg.baseline_prompt:
            system = SYSTEM_PROMPT
            prompt = baseline_user_prompt(
                obs, self.state.observations(), notes,
                min_replicas=cfg.min_replicas, max_replicas=cfg.max_replicas,
            )
        else:
            system = ADVANCED_SYSTEM
            view = StateSnapshot(
                observation=view.observation,
                trends=self.state.trends(),
                forecast=view.forecast,
                startup=view.startup,
                recent_actions=self.state.recent_actions(),
                recent_outcomes=self.state.recent_outcomes(),
                context_notes=notes,
                context_event=view.context_event,
            )
            prompt = advanced_user_prompt(view, cfg)

        response = self.client.complete(system, prompt)
        provider = getattr(self.client, "provider_name", "")
        model = getattr(self.client, "model", "") or ""
        if response is None:
            return None, "model unavailable", True, None, model, "fallback"
        if cfg.baseline_prompt:
            desired, reason, invalid = baseline_desired(response.text, obs.desired_replicas or 0)
        else:
            desired, reason, invalid = parse_advanced(response.text, obs.desired_replicas or 0)
        source = "fake" if provider == "fake" else "llm"
        if invalid:
            source = "fallback"
        return desired, reason, invalid, response.latency_s, model, source

    def _maybe_outcome(self, obs: ClusterObservation, now: float) -> None:
        pending = self._awaiting_outcome
        if not pending or now - pending["unix_time"] < 45:
            return
        before = pending.get("p95_latency_ms")
        after = obs.p95_latency_ms
        if before is None or after is None:
            latency_trend = "unknown"
        elif after < before - 1:
            latency_trend = "improving"
        elif after > before + 1:
            latency_trend = "worsening"
        else:
            latency_trend = "stable"
        self.state.record_outcome({
            "requested_replicas": pending["requested_replicas"],
            "ready_replicas_after_45s": obs.ready_replicas,
            "latency_trend": latency_trend,
        })
        self._awaiting_outcome = None


def _action(current: int, target: int) -> str:
    if target > current:
        return "scale_up"
    if target < current:
        return "scale_down"
    return "hold"


def _forecast_dict(forecast) -> dict:
    return {
        "horizon_seconds": forecast.horizon_seconds,
        "current_rps": forecast.current_rps,
        "forecast_rps": forecast.forecast_rps,
        "method": forecast.method,
        "valid": forecast.valid,
        "samples": forecast.samples,
    }
