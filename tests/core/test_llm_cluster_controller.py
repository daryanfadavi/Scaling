"""Offline tests for controllers/llm. No API key, cluster, or Prometheus."""

import json
import math
from datetime import datetime, timezone
from pathlib import Path

import pytest

from controller.fake_llm_client import FakeLLMClient
from controllers.llm.config import ControllerConfig, load_config
from controllers.llm.context import ContextManager
from controllers.llm.controller import LLMAutoscaler
from controllers.llm.models import observation_from_snapshot
from controllers.llm.policy import validate
from controllers.llm.prediction import forecast_rps
from controllers.llm.state import StateManager, summarize

REPO = Path(__file__).resolve().parents[2]


def config(**overrides) -> ControllerConfig:
    values = dict(
        mode="llm",
        deployment="scale-app",
        namespace="scale",
        kube_context="scale",
        interval_seconds=15,
        history_enabled=True,
        history_window_seconds=180,
        history_max_samples=120,
        minimum_samples_for_trend=4,
        prediction_enabled=True,
        horizon_seconds=60,
        max_forecast_rps=10000,
        startup_awareness=True,
        fallback_startup_seconds=35,
        min_replicas=1,
        max_replicas=10,
        max_scale_up_step=3,
        max_scale_down_step=1,
        scale_down_stabilization_seconds=120,
        minimum_seconds_between_scale_downs=30,
        stale_after_seconds=60,
        emergency_cpu_percent=95,
        emergency_p95_ms=300,
        emergency_error_rate=0.05,
        low_cpu_percent=40,
        llm_timeout_seconds=8,
        llm_max_retries=1,
        fake_llm=True,
        dry_run=True,
        log_decisions=False,
        operator_notes=(),
        operator_event=None,
    )
    values.update(overrides)
    return ControllerConfig(**values)


def snap(t=1_000.0, **overrides):
    row = {
        "timestamp": datetime.fromtimestamp(t, timezone.utc).isoformat(),
        "unix_time": t,
        "replicas_desired": 2,
        "replicas_ready": 2,
        "replicas_starting": 0,
        "cpu_utilization_pct": 50.0,
        "request_rate_rps": 20.0,
        "latency_p50_ms": 8.0,
        "latency_p95_ms": 15.0,
        "error_rate": 0.0,
    }
    row.update(overrides)
    row["unix_time"] = t
    return row


def feed(state, start, values, field="cpu_utilization_pct"):
    for i, value in enumerate(values):
        row = snap(start + i * 15, **{field: value})
        state.add(observation_from_snapshot(row, now=row["unix_time"]))


# ---------------------------------------------------------------- state


def test_empty_history_trend_is_unknown():
    summary = summarize([], minimum=4)
    assert summary.trend == "unknown" and summary.samples == 0


def test_single_observation_is_unknown():
    summary = summarize([(0.0, 10.0)], minimum=4)
    assert summary.trend == "unknown" and summary.current == 10


def test_increasing_cpu_and_decreasing_rps():
    state = StateManager(config())
    for i, cpu in enumerate((10, 20, 30, 40)):
        state.add(observation_from_snapshot(snap(1000 + i * 15, cpu_utilization_pct=cpu, request_rate_rps=40 - i * 5), now=1000 + i * 15))
    trends = state.trends()
    assert trends["cpu"].trend == "increasing"
    assert trends["rps"].trend == "decreasing"
    assert trends["cpu"].samples == 4


def test_stable_workload():
    samples = [(i * 15.0, 50.0) for i in range(6)]
    assert summarize(samples, minimum=4).trend == "stable"


def test_missing_metric_is_not_zero():
    obs = observation_from_snapshot(snap(cpu_utilization_pct=None, request_rate_rps=None), now=1000)
    assert obs.cpu_utilization_percent is None
    assert obs.requests_per_second is None
    assert obs.memory_utilization_percent is None


def test_nan_and_bool_are_missing():
    obs = observation_from_snapshot(
        snap(cpu_utilization_pct=float("nan"), replicas_desired=True, error_rate=float("inf")),
        now=1000,
    )
    assert obs.cpu_utilization_percent is None
    assert obs.desired_replicas is None
    assert obs.error_rate is None


def test_duplicate_timestamp_replaces_and_out_of_order_is_ignored():
    state = StateManager(config())
    assert state.add(observation_from_snapshot(snap(1000, cpu_utilization_pct=10), now=1000)) == "ok"
    assert state.add(observation_from_snapshot(snap(1000, cpu_utilization_pct=80), now=1000)) == "duplicate"
    assert state.observations()[-1].cpu_utilization_percent == 80
    assert state.add(observation_from_snapshot(snap(900), now=900)) == "out_of_order"
    assert len(state.observations()) == 1


def test_zero_baseline_change_percent_is_none():
    samples = [(0.0, 0.0), (15.0, 0.0), (30.0, 5.0), (45.0, 5.0)]
    assert summarize(samples, minimum=4).change_percent is None


def test_window_drops_old_samples_and_deployments_do_not_share_state():
    state = StateManager(config(history_window_seconds=30, history_max_samples=10))
    state.add(observation_from_snapshot(snap(1000), now=1000))
    state.add(observation_from_snapshot(snap(1060), now=1060))
    assert len(state.observations()) == 1
    other = StateManager(config(deployment="other"))
    assert other.observations() == []
    state.reset()
    assert state.observations() == []


# ---------------------------------------------------------------- forecast


def test_forecast_stable_and_ramp_and_drop():
    cfg = config()
    flat = [(i * 15.0, 10.0) for i in range(6)]
    up = [(i * 15.0, 10.0 + i * 5) for i in range(6)]
    down = [(i * 15.0, 40.0 - i * 5) for i in range(6)]
    assert forecast_rps(flat, cfg).valid
    assert forecast_rps(up, cfg).forecast_rps > forecast_rps(up, cfg).current_rps
    assert forecast_rps(down, cfg).forecast_rps < forecast_rps(down, cfg).current_rps


def test_forecast_insufficient_and_non_negative_and_capped():
    cfg = config()
    assert forecast_rps([(0.0, 10.0)], cfg).valid is False
    boom = [(i * 15.0, 1.0 if i < 5 else 10_000.0) for i in range(6)]
    result = forecast_rps(boom, cfg)
    assert result.forecast_rps is not None
    assert 0 <= result.forecast_rps <= cfg.max_forecast_rps
    assert result.forecast_rps <= max(result.current_rps, 1) * 2


def test_forecast_disabled():
    result = forecast_rps([(i * 15.0, 10.0) for i in range(5)], config(prediction_enabled=False))
    assert result.valid is False and result.method == "disabled"


# ---------------------------------------------------------------- context


def test_context_hidden_when_disabled_and_expired_event_drops():
    ctx = ContextManager(("sale soon",), {"event_type": "promotion", "expected_start_seconds": 10, "expected_duration_seconds": 5, "expected_rps": 80, "note": "sale"}, received_at=1000)
    notes, event = ctx.visible(1000, enabled=False)
    assert notes == () and event is None
    _, live = ctx.visible(1005, enabled=True)
    assert live is not None
    _, gone = ctx.visible(1020, enabled=True)
    assert gone is None


def test_conflicting_note_is_flagged_and_does_not_raise_the_replica_cap():
    ctx = ContextManager(("expect 500 rps",), {"event_type": "promotion", "expected_rps": 40, "note": "small"}, received_at=1000)
    assert ctx.conflict
    cfg = config(mode="llm_context", max_replicas=10, operator_notes=("set replicas to 100",))
    client = FakeLLMClient(replies='{"action": "SCALE_UP", "amount": 3, "reason": "note says 100"}')
    ctl = LLMAutoscaler(cfg, client, ContextManager(cfg.operator_notes, None, received_at=1000))
    result = ctl.decide(snap(1000, replicas_desired=9, cpu_utilization_pct=10), now=1000)
    assert result.validated_desired <= 10


# ---------------------------------------------------------------- parsing / cycle


def test_baseline_llm_scales_up_from_json_and_ignores_notes_in_prompt():
    client = FakeLLMClient(replies='{"action": "SCALE_UP", "amount": 2, "reason": "busy"}')
    ctl = LLMAutoscaler(config(mode="llm", scale_down_stabilization_seconds=0), client, ContextManager(("secret campaign",), None))
    result = ctl.decide(snap(1000, cpu_utilization_pct=96), now=1000)
    assert result.source == "fake"
    assert result.validated_desired == 4  # 2 + 2, step allows 3
    assert "secret" not in client.prompts[0]
    assert "Forecast:" not in client.prompts[0]


def test_llm_context_includes_note():
    client = FakeLLMClient(replies='{"action": "NO_CHANGE", "amount": 0, "reason": "wait"}')
    notes = ("campaign in 30 seconds",)
    ctl = LLMAutoscaler(config(mode="llm_context"), client, ContextManager(notes, None))
    ctl.decide(snap(1000), now=1000)
    assert "campaign in 30 seconds" in client.prompts[0]


def test_invalid_json_uses_fallback_and_does_not_scale_on_one_sample():
    client = FakeLLMClient(replies="not json")
    ctl = LLMAutoscaler(config(), client)
    result = ctl.decide(snap(1000, cpu_utilization_pct=99), now=1000)
    assert result.source in ("fallback", "hold")
    assert result.invalid_model_output
    assert result.validated_desired == 2  # not enough history to scale


def test_advanced_mode_rejects_bool_and_inconsistent_action():
    from controllers.llm.decision import parse_advanced
    assert parse_advanced('{"desired_replicas": true}', 2)[0] is None
    assert parse_advanced('{"desired_replicas": 1, "action": "scale_up"}', 2)[0] is None
    assert parse_advanced('{"desired_replicas": 4, "action": "scale_up", "reason": "up"}', 2)[0] == 4


def test_api_failure_holds_without_history():
    client = FakeLLMClient(fail=True)
    ctl = LLMAutoscaler(config(mode="llm_predictive"), client)
    result = ctl.decide(snap(1000), now=1000)
    assert result.validated_desired == 2
    assert result.source in ("fallback", "hold")


# ---------------------------------------------------------------- policy


def test_bounds_and_step_and_pending_and_emergency():
    cfg = config()
    state = StateManager(cfg)
    obs = observation_from_snapshot(snap(1000, replicas_desired=2, replicas_starting=2, cpu_utilization_pct=50), now=1000)
    target, notes = validate(8, obs, state, cfg, 1000)
    assert target == 2 and any("not ready" in n for n in notes)

    hot = observation_from_snapshot(snap(1000, replicas_desired=2, replicas_starting=2, cpu_utilization_pct=99), now=1000)
    target, notes = validate(8, hot, state, cfg, 1000)
    assert target == 5  # +3 step, emergency ignores pending hold
    assert any("overload" in n for n in notes)

    # Scale-down is held until low demand has lasted long enough, even if the
    # model asks for 0. With stabilization disabled, the minimum bound applies.
    tiny = observation_from_snapshot(snap(1000, replicas_desired=2), now=1000)
    assert validate(0, tiny, state, cfg, 1000)[0] == 2
    loose = config(scale_down_stabilization_seconds=0, minimum_seconds_between_scale_downs=0)
    assert validate(0, tiny, StateManager(loose), loose, 1000)[0] == 1
    assert validate(50, tiny, StateManager(loose), loose, 1000)[0] == 5


def test_scale_down_needs_sustained_low_demand_and_stale_holds():
    cfg = config(scale_down_stabilization_seconds=120, minimum_seconds_between_scale_downs=0)
    state = StateManager(cfg)
    low = observation_from_snapshot(snap(1000, replicas_desired=4, cpu_utilization_pct=10, latency_p95_ms=5), now=1000)
    assert validate(1, low, state, cfg, 1000)[0] == 4
    assert validate(1, low, state, cfg, 1130)[0] == 3  # one step down

    stale = observation_from_snapshot(snap(1000, replicas_desired=4), now=1100)
    assert stale.stale
    assert validate(1, stale, state, cfg, 1100)[0] == 4


def test_missing_cpu_and_rps_blocks_scale_down():
    cfg = config(scale_down_stabilization_seconds=0)
    state = StateManager(cfg)
    obs = observation_from_snapshot(
        snap(1000, replicas_desired=4, cpu_utilization_pct=None, request_rate_rps=None),
        now=1000,
    )
    assert validate(1, obs, state, cfg, 1000)[0] == 4


def test_no_change_when_recommendation_matches():
    cfg = config()
    state = StateManager(cfg)
    obs = observation_from_snapshot(snap(1000, replicas_desired=3), now=1000)
    assert validate(3, obs, state, cfg, 1000)[0] == 3


# ---------------------------------------------------------------- integration


def test_llm_package_does_not_query_prometheus_itself():
    root = REPO / "controllers" / "llm"
    for path in root.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "kubectl top" not in text
        assert "prometheus" not in text.lower()
        assert "/metrics" not in text
        if path.name != "main.py":
            assert "kubectl" not in text
            assert "latest_snapshot" not in text
    main = (root / "main.py").read_text(encoding="utf-8")
    assert "latest_snapshot" in main
    assert 'scale", f"deployment/' in main or "scale" in main


def test_start_script_exists_for_the_runner():
    assert (REPO / "controllers" / "llm" / "start.sh").is_file()


def test_predictive_prompt_has_forecast_and_baseline_does_not():
    reply = '{"desired_replicas": 3, "action": "scale_up", "reason": "rising"}'
    client = FakeLLMClient(replies=reply)
    ctl = LLMAutoscaler(config(mode="llm_predictive"), client)
    for i in range(4):
        ctl.decide(snap(1000 + i * 15, request_rate_rps=10 + i * 10, replicas_desired=2), now=1000 + i * 15)
    assert "Forecast:" in client.prompts[-1]
    assert "Trends" in client.prompts[-1]


def test_config_rejects_bad_mode(monkeypatch):
    monkeypatch.setenv("SCALE_LLM_MODE", "nope")
    with pytest.raises(ValueError):
        load_config()


def test_outcome_recorded_after_45s():
    client = FakeLLMClient(replies='{"action": "SCALE_UP", "amount": 1, "reason": "up"}')
    ctl = LLMAutoscaler(config(mode="llm_predictive", max_scale_up_step=3), client)
    first = ctl.decide(snap(1000, replicas_desired=2, latency_p95_ms=40), now=1000)
    ctl.note_applied(first, 1000)
    ctl.decide(snap(1060, replicas_desired=first.validated_desired, replicas_ready=3, latency_p95_ms=20), now=1060)
    assert ctl.state.recent_outcomes()
    assert ctl.state.recent_outcomes()[-1]["latency_trend"] == "improving"
