"""The cluster LLM controller (controllers/llm/): guardrails, prompt, and the decision loop.

No cluster, Prometheus or API key needed: telemetry, kubectl and the model are faked.
"""

import threading

import pytest

import controllers.llm.main as llm_main
from controller.llm_client import LLMResponse
from controller.llm_controller import ParsedAction
from controllers.llm.main import (
    COOLDOWN_AFTER_SCALE_S,
    SCALE_DOWN_COOLDOWN_S,
    Controller,
    apply_guardrails,
)
from controllers.llm.prompt import SYSTEM_PROMPT, build_prompt

UP2 = ParsedAction("SCALE_UP", 2, "busy")
DOWN1 = ParsedAction("SCALE_DOWN", 1, "idle")
HOLD = ParsedAction("NO_CHANGE", 0, "fine")

# ------------------------------------------------------------ guardrails


def test_scale_up_and_down_apply_when_never_scaled_before():
    assert apply_guardrails(UP2, 3, now=100, last_scale_time=None) == (5, "")
    assert apply_guardrails(DOWN1, 3, now=100, last_scale_time=None) == (2, "")


def test_no_change_holds():
    assert apply_guardrails(HOLD, 4, now=0, last_scale_time=None) == (4, "")


def test_bounds_clamp_to_min_and_max():
    target, note = apply_guardrails(UP2, 9, now=0, last_scale_time=None)
    assert target == 10 and "clamped" in note
    target, note = apply_guardrails(ParsedAction("SCALE_DOWN", 3, ""), 2, now=0, last_scale_time=None)
    assert target == 1 and "clamped" in note


def test_never_goes_outside_bounds_for_any_action():
    for current in range(1, 11):
        for action in ("SCALE_UP", "SCALE_DOWN", "NO_CHANGE"):
            for amount in (0, 1, 2, 3):
                target, _ = apply_guardrails(ParsedAction(action, amount, ""), current, 0, None)
                assert 1 <= target <= 10


def test_any_scale_blocked_during_short_cooldown():
    just_after = COOLDOWN_AFTER_SCALE_S - 1
    assert apply_guardrails(UP2, 3, now=just_after, last_scale_time=0)[0] == 3
    assert apply_guardrails(DOWN1, 3, now=just_after, last_scale_time=0)[0] == 3
    assert apply_guardrails(UP2, 3, now=COOLDOWN_AFTER_SCALE_S, last_scale_time=0)[0] == 5


def test_scale_down_has_a_longer_cooldown_than_scale_up():
    assert SCALE_DOWN_COOLDOWN_S > COOLDOWN_AFTER_SCALE_S
    mid = SCALE_DOWN_COOLDOWN_S - 1
    target, note = apply_guardrails(DOWN1, 5, now=mid, last_scale_time=0)
    assert target == 5 and "scale-down cooldown" in note
    assert apply_guardrails(UP2, 5, now=mid, last_scale_time=0)[0] == 7
    assert apply_guardrails(DOWN1, 5, now=SCALE_DOWN_COOLDOWN_S, last_scale_time=0)[0] == 4


# ------------------------------------------------------------ prompt

SNAPSHOT = {
    "replicas_ready": 3.0, "replicas_starting": 1.0, "cpu_utilization_pct": 142.0,
    "request_rate_rps": 27.5, "latency_p50_ms": 12.0, "latency_p95_ms": 90.0, "error_rate": 0.0,
}


def test_prompt_shows_the_snapshot_and_bounds():
    text = build_prompt(SNAPSHOT, current_replicas=4, min_replicas=1, max_replicas=10, history=[])
    assert "4 requested, 3 ready, 1 starting (min 1, max 10)" in text
    assert "CPU: 142%" in text and "27.5 req/s" in text and "p95 90 ms" in text
    assert "recent decisions" not in text  # no history section yet


def test_prompt_marks_missing_data_instead_of_inventing_it():
    empty = {k: None for k in SNAPSHOT}
    text = build_prompt(empty, 2, 1, 10, [])
    assert "n/a" in text and "None" not in text and "nan" not in text.lower()


def test_prompt_lists_history_oldest_first():
    history = [{**SNAPSHOT, "replicas_requested": 2, "action": "SCALE_UP 2"},
               {**SNAPSHOT, "replicas_requested": 4, "action": "NO_CHANGE"}]
    lines = build_prompt(SNAPSHOT, 4, 1, 10, history).splitlines()
    rows = [line for line in lines if line.endswith(("SCALE_UP 2", "NO_CHANGE"))]
    assert rows[0].startswith("2 |") and rows[1].startswith("1 |")  # ticks ago, oldest first


def test_nothing_about_the_experiment_reaches_the_model():
    text = (SYSTEM_PROMPT + build_prompt(SNAPSHOT, 4, 1, 10, [])).lower()
    for word in ("scenario", "hpa", "keda", "baseline", "locust", "experiment", "benchmark"):
        assert word not in text


# ------------------------------------------------------------ the loop


class FakeClient:
    model = "fake-model"

    def __init__(self, reply):
        self.reply = reply
        self.prompts = []

    def complete(self, system, prompt, use_cache=True):
        self.prompts.append(prompt)
        return None if self.reply is None else LLMResponse(self.reply, 0.5, False, "fake-model")


@pytest.fixture
def cluster(monkeypatch):
    """Fake telemetry + Kubernetes: `state` holds the replica count; `scaled` logs scale calls."""
    state = {"replicas": 3, "scaled": [], "snapshot": dict(SNAPSHOT), "observe_error": None}

    def fake_snapshot():
        if state["observe_error"]:
            raise RuntimeError(state["observe_error"])
        return state["snapshot"]

    def fake_scale(target):
        state["scaled"].append(target)
        state["replicas"] = target

    monkeypatch.setattr(llm_main, "latest_snapshot", fake_snapshot)
    monkeypatch.setattr(llm_main, "read_current_replicas", lambda: state["replicas"])
    monkeypatch.setattr(llm_main, "scale_to", fake_scale)
    return state


def make_controller(reply, clock):
    return Controller(FakeClient(reply), threading.Event(), clock=clock)


def test_valid_scale_up_is_applied(cluster):
    c = make_controller('{"action": "SCALE_UP", "amount": 2, "reason": "busy"}', lambda: 0.0)
    record = c.step()
    assert cluster["scaled"] == [5]
    assert record["applied"] and record["replicas_before"] == 3 and record["replicas_target"] == 5
    assert record["model"] == "fake-model" and record["llm_latency_s"] == 0.5


@pytest.mark.parametrize("reply", [None, "", "I think we should scale up", '{"action": "SCALE_UP", "amount": 99}',
                                   '{"action": "DELETE_EVERYTHING", "amount": 1}'])
def test_unusable_model_output_changes_nothing(cluster, reply):
    record = make_controller(reply, lambda: 0.0).step()
    assert cluster["scaled"] == []
    assert record["invalid"] is True and record["applied"] is False


def test_out_of_range_amount_never_reaches_kubernetes(cluster):
    cluster["replicas"] = 9
    make_controller('{"action": "SCALE_UP", "amount": 3, "reason": "x"}', lambda: 0.0).step()
    assert cluster["scaled"] == [10]  # clamped to max, not 12


def test_cooldown_blocks_a_second_scale(cluster):
    now = [0.0]
    c = make_controller('{"action": "SCALE_UP", "amount": 1, "reason": "x"}', lambda: now[0])
    c.step()
    now[0] = 15.0
    record = c.step()
    assert cluster["scaled"] == [4]  # the second decision was held
    assert "cooldown" in record["guardrail"] and record["applied"] is False
    now[0] = 45.0
    c.step()
    assert cluster["scaled"] == [4, 5]


def test_failed_observation_holds_and_does_not_crash(cluster):
    cluster["observe_error"] = "prometheus down"
    c = make_controller('{"action": "SCALE_UP", "amount": 1, "reason": "x"}', lambda: 0.0)
    record = c.step()
    assert cluster["scaled"] == [] and "observe failed" in record["error"]
    assert c.client.prompts == []  # didn't waste a model call on no data


def test_decision_made_after_sigterm_is_not_applied(cluster):
    c = make_controller('{"action": "SCALE_UP", "amount": 1, "reason": "x"}', lambda: 0.0)
    c.stop.set()
    record = c.step()
    assert cluster["scaled"] == [] and record["applied"] is False


def test_model_sees_its_own_blocked_and_applied_history(cluster):
    now = [0.0]
    c = make_controller('{"action": "SCALE_UP", "amount": 1, "reason": "x"}', lambda: now[0])
    c.step()
    now[0] = 15.0
    c.step()
    third_prompt_input = c.client.prompts[1]
    assert "SCALE_UP 1" in third_prompt_input  # tick 1 shows what happened at tick 0
    now[0] = 30.0
    c.step()
    last = c.client.prompts[2]
    assert "blocked by guardrails" in last


def test_scale_to_refuses_out_of_bounds_targets():
    with pytest.raises(ValueError):
        llm_main.scale_to(11)
    with pytest.raises(ValueError):
        llm_main.scale_to(0)
