"""Spec for controller/hpa_baseline.py (Pair A).

Run with:  pytest -m todo tests/todo/test_hpa.py
All of these should pass once HPAController.decide is implemented.
"""

import pytest

from controller.hpa_baseline import HPAController
from tests.helpers import make_obs

pytestmark = pytest.mark.todo


def decide(controller, replicas, cpu, tick=0):
    return controller.decide(
        make_obs(replicas_ready=replicas, cpu_pct=cpu, tick=tick)
    ).target_replicas


def test_worked_example_scale_up():
    # 3 replicas at 90% with a 60% target: ceil(3 * 90/60) = ceil(4.5) = 5
    assert decide(HPAController(), replicas=3, cpu=90) == 5


def test_scale_down():
    # 4 replicas at 45%: ceil(4 * 45/60) = ceil(3.0) = 3
    assert decide(HPAController(), replicas=4, cpu=45) == 3


@pytest.mark.parametrize("cpu", [55, 60, 63, 65])
def test_inside_tolerance_keeps_current(cpu):
    # ratio within 0.9..1.1 -> no change
    assert decide(HPAController(), replicas=4, cpu=cpu) == 4


def test_just_outside_tolerance_scales():
    # 70 / 60 = 1.17 -> outside the 10% band -> ceil(4 * 1.17) = 5
    assert decide(HPAController(), replicas=4, cpu=70) == 5


def test_scale_up_is_immediate_even_after_high_recommendations():
    hpa = HPAController()
    assert decide(hpa, replicas=2, cpu=90, tick=0) == 3
    assert decide(hpa, replicas=3, cpu=100, tick=1) == 5


def test_scale_down_held_by_stabilization_window():
    hpa = HPAController()
    assert decide(hpa, replicas=4, cpu=90, tick=0) == 6  # recommendation 6
    # CPU drops: raw recommendation is ceil(6 * 30/60) = 3, but the max over the
    # last 20 ticks is still 6, so HPA holds.
    for tick in range(1, 20):
        assert (
            decide(hpa, replicas=6, cpu=30, tick=tick) == 6
        ), f"scaled down too early at tick {tick}"
    # By now the tick-0 recommendation has left the 20-tick window.
    assert decide(hpa, replicas=6, cpu=30, tick=20) == 3


def test_reset_clears_the_window():
    hpa = HPAController()
    decide(hpa, replicas=4, cpu=90, tick=0)  # recommendation 6
    hpa.reset()
    assert decide(hpa, replicas=6, cpu=30, tick=0) == 3


def test_decision_has_a_reason():
    d = HPAController().decide(make_obs(replicas_ready=3, cpu_pct=90))
    assert d.info.get("reason")


def test_ignores_operator_notes():
    a = HPAController().decide(make_obs(replicas_ready=3, cpu_pct=90))
    b = HPAController().decide(
        make_obs(replicas_ready=3, cpu_pct=90, operator_notes=["10x traffic soon!"])
    )
    assert a.target_replicas == b.target_replicas
