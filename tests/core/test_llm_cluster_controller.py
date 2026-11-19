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
        reconcile_timeout_seconds=60,
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

    # 50% CPU is not low demand, so scale-down waits. With the timer disabled
    # and CPU actually low, the minimum bound applies.
    tiny = observation_from_snapshot(snap(1000, replicas_desired=2), now=1000)
    assert validate(0, tiny, state, cfg, 1000)[0] == 2
    calm = observation_from_snapshot(snap(1000, replicas_desired=2, cpu_utilization_pct=10), now=1000)
    loose = config(scale_down_stabilization_seconds=0, minimum_seconds_between_scale_downs=0)
    assert validate(0, calm, StateManager(loose), loose, 1000)[0] == 1
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


def test_each_mode_is_its_own_plugin():
    """The results folder is the plugin name, so the mode cannot be only an env var."""
    plugins = {
        "llm": ("llm", "0"),
        "llm_context": ("llm_context", "0"),
        "llm_history": ("llm_history", "0"),
        "llm_predictive": ("llm_predictive", "0"),
        "llm_predictive_context": ("llm_predictive_context", "0"),
        "llm_fake": ("llm", "1"),
    }
    for folder, (mode, fake) in plugins.items():
        text = (REPO / "controllers" / folder / "start.sh").read_text(encoding="utf-8")
        assert f"export SCALE_LLM_MODE={mode}\n" in text
        assert f"export SCALE_LLM_FAKE={fake}\n" in text
        assert "python -m controllers.llm.main" in text


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


def _low(t, desired=6, ready=6):
    return snap(
        t,
        replicas_desired=desired,
        replicas_ready=ready,
        replicas_starting=0,
        cpu_utilization_pct=10,
        request_rate_rps=2,
        latency_p95_ms=12,
        error_rate=0.0,
    )


def test_stale_desired_cannot_overwrite_an_accepted_scale():
    """The 3-minute failure: accept 6, next snapshot still says 3, model asks for 4."""
    replies = [
        '{"action": "SCALE_UP", "amount": 3, "reason": "load"}',
        '{"action": "SCALE_UP", "amount": 1, "reason": "stale baseline"}',
        '{"action": "NO_CHANGE", "amount": 0, "reason": "caught up"}',
        '{"action": "SCALE_DOWN", "amount": 3, "reason": "idle"}',
        '{"action": "SCALE_DOWN", "amount": 3, "reason": "still idle"}',
        '{"action": "SCALE_DOWN", "amount": 3, "reason": "idle long enough"}',
    ]
    ctl = LLMAutoscaler(config(minimum_seconds_between_scale_downs=0), FakeLLMClient(replies=replies))
    start = 10_000.0

    first = ctl.decide(snap(start, replicas_desired=3, replicas_ready=3, cpu_utilization_pct=50), now=start)
    assert first.raw_desired == 6 and first.validated_desired == 6 and first.submit_scale
    ctl.note_applied(first, start)

    stale = ctl.decide(snap(start + 15, replicas_desired=3, replicas_ready=3, cpu_utilization_pct=50), now=start + 15)
    assert stale.reconciliation == "pending"
    assert stale.last_acknowledged_target == 6
    assert stale.raw_desired == 4
    assert stale.validated_desired == 6 and stale.submit_scale is False
    assert any("not reconciled" in note for note in stale.safety_notes)

    caught = ctl.decide(snap(start + 30, replicas_desired=6, replicas_ready=3, cpu_utilization_pct=50), now=start + 30)
    assert caught.reconciliation == "reconciled"
    assert caught.validated_desired == 6 and caught.submit_scale is False
    assert caught.observed_ready == 3

    down = ctl.decide(_low(start + 45), now=start + 45)
    assert down.validated_desired == 6 and down.submit_scale is False
    early = ctl.decide(_low(start + 100), now=start + 100)
    assert early.validated_desired == 6 and early.submit_scale is False
    allowed = ctl.decide(_low(start + 45 + 120), now=start + 45 + 120)
    assert allowed.validated_desired == 5 and allowed.submit_scale and allowed.action == "scale_down"


def test_overload_scale_up_is_allowed_before_reconciliation():
    replies = [
        '{"action": "SCALE_UP", "amount": 2, "reason": "up"}',
        '{"action": "SCALE_UP", "amount": 3, "reason": "hot"}',
    ]
    ctl = LLMAutoscaler(config(), FakeLLMClient(replies=replies))
    first = ctl.decide(snap(1000, replicas_desired=2, replicas_ready=2, cpu_utilization_pct=50), now=1000)
    ctl.note_applied(first, 1000)
    hot = ctl.decide(snap(1015, replicas_desired=2, replicas_ready=2, cpu_utilization_pct=99), now=1015)
    assert hot.reconciliation == "pending" and hot.submit_scale
    assert hot.validated_desired == 5  # accepted 4, plus one, inside the +3 step


def test_extra_scale_up_waits_while_unreconciled_and_not_overloaded():
    replies = [
        '{"action": "SCALE_UP", "amount": 2, "reason": "up"}',
        '{"action": "SCALE_UP", "amount": 5, "reason": "more"}',
    ]
    ctl = LLMAutoscaler(config(), FakeLLMClient(replies=replies))
    first = ctl.decide(snap(1000, replicas_desired=2, replicas_ready=2), now=1000)
    ctl.note_applied(first, 1000)
    nxt = ctl.decide(snap(1015, replicas_desired=2, replicas_ready=2, cpu_utilization_pct=50), now=1015)
    assert nxt.validated_desired == 4 and nxt.submit_scale is False


def test_reconcile_timeout_adopts_the_observed_count():
    replies = [
        '{"action": "SCALE_UP", "amount": 2, "reason": "up"}',
        '{"action": "NO_CHANGE", "amount": 0, "reason": "give up"}',
    ]
    ctl = LLMAutoscaler(config(reconcile_timeout_seconds=30), FakeLLMClient(replies=replies))
    first = ctl.decide(snap(1000, replicas_desired=2, replicas_ready=2), now=1000)
    ctl.note_applied(first, 1000)
    later = ctl.decide(snap(1030, replicas_desired=2, replicas_ready=2), now=1030)
    assert later.reconciliation == "reconcile_timeout"
    assert ctl.state.acknowledged_target == 2
    assert later.submit_scale is False


def test_external_replica_change_becomes_the_new_baseline():
    replies = [
        '{"action": "SCALE_UP", "amount": 1, "reason": "up"}',
        '{"action": "NO_CHANGE", "amount": 0, "reason": "ok"}',
        '{"action": "NO_CHANGE", "amount": 0, "reason": "someone else"}',
    ]
    ctl = LLMAutoscaler(config(), FakeLLMClient(replies=replies))
    first = ctl.decide(snap(1000, replicas_desired=2, replicas_ready=2), now=1000)
    ctl.note_applied(first, 1000)
    caught = ctl.decide(snap(1015, replicas_desired=first.validated_desired, replicas_ready=3), now=1015)
    assert caught.reconciliation == "reconciled"
    changed = ctl.decide(snap(1030, replicas_desired=1, replicas_ready=1), now=1030)
    assert changed.reconciliation == "external_change"
    assert ctl.state.acknowledged_target == 1


def test_rejected_scale_does_not_move_the_accepted_target(monkeypatch):
    from controllers.llm.main import run_once

    def _deny(config, replicas):
        raise RuntimeError("denied")

    monkeypatch.setattr("controllers.llm.main.scale_to", _deny)
    ctl = LLMAutoscaler(config(dry_run=False), FakeLLMClient(replies='{"action": "SCALE_UP", "amount": 1, "reason": "up"}'))
    result = run_once(ctl, ctl.config, snap(datetime.now(timezone.utc).timestamp(), replicas_desired=2, cpu_utilization_pct=80))
    assert result.submit_scale and result.applied is False
    assert "denied" in result.apply_error
    assert ctl.state.acknowledged_target is None


def test_reset_clears_reconciliation_and_history():
    ctl = LLMAutoscaler(config(), FakeLLMClient(replies='{"action": "SCALE_UP", "amount": 1, "reason": "up"}'))
    first = ctl.decide(snap(1000, replicas_desired=2), now=1000)
    ctl.note_applied(first, 1000)
    ctl.reset()
    assert ctl.state.acknowledged_target is None
    assert ctl.state.observations() == []
    assert ctl.state.reconciliation_pending is False


def test_unready_requested_pods_block_another_scale_up_until_the_window_ends():
    replies = [
        '{"action": "SCALE_UP", "amount": 2, "reason": "up"}',
        '{"action": "NO_CHANGE", "amount": 0, "reason": "wait"}',
        '{"action": "SCALE_UP", "amount": 1, "reason": "still short"}',
        '{"action": "SCALE_UP", "amount": 1, "reason": "window elapsed"}',
    ]
    ctl = LLMAutoscaler(config(fallback_startup_seconds=35), FakeLLMClient(replies=replies))
    first = ctl.decide(snap(1000, replicas_desired=2, replicas_ready=2, cpu_utilization_pct=60), now=1000)
    ctl.note_applied(first, 1000)
    caught = ctl.decide(snap(1010, replicas_desired=4, replicas_ready=2, replicas_starting=2, cpu_utilization_pct=60), now=1010)
    assert caught.reconciliation == "reconciled"
    early = ctl.decide(snap(1020, replicas_desired=4, replicas_ready=2, replicas_starting=0, cpu_utilization_pct=60), now=1020)
    assert early.validated_desired == 4 and early.submit_scale is False
    late = ctl.decide(snap(1060, replicas_desired=4, replicas_ready=2, replicas_starting=0, cpu_utilization_pct=60), now=1060)
    assert late.validated_desired == 5 and late.submit_scale


def test_missing_readiness_does_not_invent_pending_pods():
    cfg = config()
    state = StateManager(cfg)
    obs = observation_from_snapshot(snap(1000, replicas_desired=3, replicas_ready=None, replicas_starting=None), now=1000)
    assert state.unready_requested(obs) is None
    assert validate(5, obs, state, cfg, 1000)[0] == 5


def test_scale_down_boundary_pause_and_vetoes():
    cfg = config(scale_down_stabilization_seconds=120, minimum_seconds_between_scale_downs=30)
    state = StateManager(cfg)
    low = observation_from_snapshot(_low(1000, desired=4, ready=4), now=1000)
    assert validate(1, low, state, cfg, 1000)[0] == 4
    assert validate(1, low, state, cfg, 1119)[0] == 4
    assert validate(1, low, state, cfg, 1120)[0] == 3

    paused = StateManager(cfg)
    assert validate(1, low, paused, cfg, 1000)[0] == 4
    missing = observation_from_snapshot(snap(1030, replicas_desired=4, replicas_ready=4, cpu_utilization_pct=None), now=1030)
    assert validate(1, missing, paused, cfg, 1030)[0] == 4
    assert paused.low_demand_seconds(1060) == 30  # paused at the missing sample, gap not counted
    assert validate(1, low, paused, cfg, 1120)[0] == 4
    assert validate(1, low, paused, cfg, 1210)[0] == 3

    hot_latency = observation_from_snapshot(
        snap(1000, replicas_desired=4, cpu_utilization_pct=10, latency_p95_ms=400), now=1000,
    )
    assert validate(1, hot_latency, StateManager(config(scale_down_stabilization_seconds=0)), config(scale_down_stabilization_seconds=0), 1000)[0] == 4


def test_brief_dip_and_alternating_demand_reset_the_timer():
    cfg = config(scale_down_stabilization_seconds=120, minimum_seconds_between_scale_downs=0)
    state = StateManager(cfg)
    low = observation_from_snapshot(_low(1000, desired=4, ready=4), now=1000)
    high = observation_from_snapshot(snap(1030, replicas_desired=4, cpu_utilization_pct=80), now=1030)
    validate(1, low, state, cfg, 1000)
    validate(1, high, state, cfg, 1030)
    validate(1, low, state, cfg, 1060)
    assert validate(1, low, state, cfg, 1179)[0] == 4
    assert validate(1, low, state, cfg, 1180)[0] == 3


def test_repeated_scale_down_respects_the_gap_between_downs():
    cfg = config(scale_down_stabilization_seconds=0, minimum_seconds_between_scale_downs=30)
    state = StateManager(cfg)
    low = observation_from_snapshot(_low(1000, desired=4, ready=4), now=1000)
    assert validate(1, low, state, cfg, 1000)[0] == 3
    state.note_scale_down(1000)
    assert validate(1, low, state, cfg, 1010)[0] == 4
    assert validate(1, low, state, cfg, 1030)[0] == 3


def test_hold_during_low_demand_does_not_reset_the_timer():
    replies = ['{"action": "NO_CHANGE", "amount": 0, "reason": "fine"}'] * 3
    ctl = LLMAutoscaler(config(minimum_seconds_between_scale_downs=0), FakeLLMClient(replies=replies))
    ctl.decide(_low(1000, desired=4, ready=4), now=1000)
    ctl.decide(_low(1060, desired=4, ready=4), now=1060)
    assert ctl.state.low_demand_seconds(1120) == 120


def test_rising_request_rate_is_not_low_demand():
    cfg = config(scale_down_stabilization_seconds=0, minimum_seconds_between_scale_downs=0)
    state = StateManager(cfg)
    for i, rps in enumerate((5, 10, 15, 20)):
        state.add(observation_from_snapshot(snap(1000 + i * 15, replicas_desired=4, cpu_utilization_pct=10, request_rate_rps=rps), now=1000 + i * 15))
    obs = observation_from_snapshot(snap(1060, replicas_desired=4, cpu_utilization_pct=10, request_rate_rps=20), now=1060)
    assert validate(1, obs, state, cfg, 1060)[0] == 4


def test_history_mode_has_trends_without_forecast_or_notes():
    client = FakeLLMClient(replies='{"desired_replicas": 2, "action": "hold", "reason": "ok"}')
    ctl = LLMAutoscaler(
        config(mode="llm_history"),
        client,
        ContextManager(("secret campaign",), None, received_at=1000),
    )
    ctl.decide(snap(1000, replicas_desired=2), now=1000)
    assert "Trends" in client.prompts[0]
    assert "Forecast:" not in client.prompts[0]
    assert "secret" not in client.prompts[0]


def test_decision_log_separates_recommendation_from_apply(capsys):
    from controllers.llm.logging import emit

    ctl = LLMAutoscaler(config(), FakeLLMClient(replies='{"action": "SCALE_UP", "amount": 1, "reason": "up"}'))
    result = ctl.decide(snap(1000, replicas_desired=2, replicas_ready=2), now=1000)
    emit(result)
    line = capsys.readouterr().out
    payload = json.loads(line)
    assert payload["raw_desired"] == 3
    assert payload["validated_desired"] == 3
    assert payload["applied"] is False
    assert payload["submit_scale"] is True
    assert "ANTHROPIC" not in line and "api_key" not in line


def test_concurrent_decisions_stay_serialized():
    import threading

    ctl = LLMAutoscaler(config(), FakeLLMClient(replies='{"action": "NO_CHANGE", "amount": 0, "reason": "ok"}'))
    errors = []

    def once():
        try:
            ctl.decide(snap(2000, replicas_desired=2), now=2000)
        except Exception as exc:  # pragma: no cover - failure is the assertion
            errors.append(exc)

    threads = [threading.Thread(target=once) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []


def test_outcome_recorded_after_45s():
    client = FakeLLMClient(replies='{"action": "SCALE_UP", "amount": 1, "reason": "up"}')
    ctl = LLMAutoscaler(config(mode="llm_predictive", max_scale_up_step=3), client)
    first = ctl.decide(snap(1000, replicas_desired=2, latency_p95_ms=40), now=1000)
    ctl.note_applied(first, 1000)
    ctl.decide(snap(1060, replicas_desired=first.validated_desired, replicas_ready=3, latency_p95_ms=20), now=1060)
    assert ctl.state.recent_outcomes()
    assert ctl.state.recent_outcomes()[-1]["latency_trend"] == "improving"
