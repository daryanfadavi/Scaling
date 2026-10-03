"""Core simulator behaviour: capacity, startup delay, backlog, guardrails, determinism."""

import pytest

from controller.base import Controller, Decision
from controller.static import StaticController
from experiments.run import resolve_scenario
from simulator.config import SimConfig
from simulator.env import ScalingEnv, apply_env_guardrails, run_episode
from simulator.metrics import summarize
from tests.helpers import OperatorNote, constant_scenario


class Scripted(Controller):
    """Returns a fixed list of targets, one per tick (then holds the last one)."""

    name = "scripted"

    def __init__(self, targets):
        self.targets = list(targets)

    def decide(self, obs):
        t = self.targets[min(obs.tick, len(self.targets) - 1)]
        return Decision(t, {"reason": "scripted"})


# ------------------------------------------------------------ capacity math


def test_capacity_and_cpu():
    env = ScalingEnv(constant_scenario(100, initial_replicas=4))
    obs = env.reset()
    assert env.state["capacity"] == 200  # 4 replicas x 50 req/s
    assert obs.cpu_pct == pytest.approx(50.0)  # 100 / 200
    assert obs.backlog == 0


def test_overload_caps_cpu_and_builds_backlog():
    # 300 req/s on 2 replicas (100 req/s capacity): 200 req/s x 15 s unserved per tick.
    env = ScalingEnv(constant_scenario(300, initial_replicas=2))
    obs = env.reset()
    assert obs.cpu_pct == 100.0
    assert obs.backlog == pytest.approx(3000)
    obs = env.step(Decision(2))
    assert obs.backlog == pytest.approx(6000)  # carried over and grew


def test_backlog_drains_when_capacity_returns():
    env = ScalingEnv(constant_scenario(150, initial_replicas=2, max_step=10))
    env.reset()  # backlog 750 after tick 0
    obs = env.step(Decision(6))
    for _ in range(3):
        obs = env.step(Decision(6))
    assert obs.replicas_ready == 6
    assert obs.backlog == 0


# ------------------------------------------------------------ startup delay


def test_new_replicas_wait_two_full_ticks():
    env = ScalingEnv(constant_scenario(100, initial_replicas=2))
    env.reset()
    obs1 = env.step(Decision(6))  # decided at tick 0
    obs2 = env.step(Decision(6))
    obs3 = env.step(Decision(6))
    assert (obs1.tick, obs1.replicas_ready, obs1.replicas_pending) == (1, 2, 4)
    assert (obs2.tick, obs2.replicas_ready, obs2.replicas_pending) == (2, 2, 4)
    assert (obs3.tick, obs3.replicas_ready, obs3.replicas_pending) == (3, 6, 0)


def test_scale_down_is_immediate():
    env = ScalingEnv(constant_scenario(100, initial_replicas=6))
    env.reset()
    obs = env.step(Decision(3))
    assert obs.replicas_ready == 3


def test_scale_down_cancels_pending_first():
    env = ScalingEnv(constant_scenario(100, initial_replicas=3))
    env.reset()
    env.step(Decision(6))  # 3 ready + 3 pending
    obs = env.step(Decision(4))  # remove 2 -> cancel 2 pending
    assert (obs.replicas_ready, obs.replicas_pending) == (3, 1)


# ------------------------------------------------------------ guardrails


@pytest.mark.parametrize(
    "requested, current, expected, clamped",
    [
        (5, 3, 5, False),  # fine
        (12, 3, 7, True),  # step capped at +4
        (0, 3, 1, True),  # raised to min_replicas
        (50, 18, 20, True),  # lowered to max_replicas
        (1, 10, 6, True),  # step capped at -4
    ],
)
def test_env_guardrails(requested, current, expected, clamped):
    target, note = apply_env_guardrails(requested, current, SimConfig())
    assert target == expected
    assert bool(note) == clamped


def test_clamping_is_logged_in_records():
    records = run_episode(
        ScalingEnv(constant_scenario(100, ticks=5)), StaticController(replicas=50)
    )
    assert records[0]["requested_target"] == 50
    assert records[0]["target"] == 6  # 2 + max_step 4
    assert "capped" in records[0]["clamp_note"]
    assert summarize(records, 15)["clamped_decisions"] > 0


def test_non_numeric_target_is_rejected():
    env = ScalingEnv(constant_scenario(100))
    env.reset()
    with pytest.raises(ValueError):
        env.step(Decision("lots"))


# ------------------------------------------------------------ observations


def test_history_holds_last_five_ticks_with_targets():
    records_env = ScalingEnv(constant_scenario(100, ticks=10))
    obs = records_env.reset()
    for _ in range(8):
        obs = records_env.step(Decision(3))
    assert len(obs.history) == 5
    assert [h["tick"] for h in obs.history] == [3, 4, 5, 6, 7]
    assert obs.history[-1]["target"] == 3


def test_operator_notes_only_while_active():
    note = OperatorNote(start_tick=2, end_tick=4, text="heads up")
    env = ScalingEnv(constant_scenario(100, ticks=6, notes=[note]))
    seen = []
    obs = env.reset()
    while obs is not None:
        seen.append(bool(obs.operator_notes))
        obs = env.step(Decision(2))
    assert seen == [False, False, True, True, False, False]


# ------------------------------------------------------------ determinism


def test_same_seed_same_results():
    scenario = resolve_scenario("spike")
    a = run_episode(ScalingEnv(scenario, seed=7), StaticController(4))
    b = run_episode(ScalingEnv(scenario, seed=7), StaticController(4))
    assert a == b


def test_load_trace_independent_of_controller():
    scenario = resolve_scenario("spike")
    a = run_episode(ScalingEnv(scenario, seed=1), StaticController(2))
    b = run_episode(
        ScalingEnv(scenario, seed=1), Scripted([2, 6, 10, 14, 18, 20, 3, 1])
    )
    assert [r["load"] for r in a] == [r["load"] for r in b]


def test_different_seed_different_trace():
    scenario = resolve_scenario("spike")
    a = run_episode(ScalingEnv(scenario, seed=1), StaticController(2))
    b = run_episode(ScalingEnv(scenario, seed=2), StaticController(2))
    assert [r["load"] for r in a] != [r["load"] for r in b]


def test_spike_and_spike_with_warning_have_identical_load():
    spike = run_episode(
        ScalingEnv(resolve_scenario("spike"), seed=0), StaticController(4)
    )
    warn = run_episode(
        ScalingEnv(resolve_scenario("spike_with_warning"), seed=0), StaticController(4)
    )
    assert [r["load"] for r in spike] == [r["load"] for r in warn]
    noted = [r["tick"] for r in warn if r["operator_notes"]]
    assert noted == list(range(24, 30))
    assert not any(r["operator_notes"] for r in spike)


@pytest.mark.parametrize("name", ["ramp", "spike", "spike_with_warning"])
def test_scenarios_have_80_ticks(name):
    assert resolve_scenario(name).ticks == 80
