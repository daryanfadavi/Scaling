"""The latency model gets worse with load and backlog, and is capped."""

import pytest

from simulator.config import SimConfig
from simulator.latency import latency_ms


def test_idle_latency_is_near_base():
    assert latency_ms(load_rate=0, capacity_rate=100, backlog=0) == pytest.approx(50.0)


def test_latency_increases_with_load():
    values = [latency_ms(load, 100, 0) for load in range(0, 100, 10)]
    assert values == sorted(values)
    assert len(set(values)) == len(values)  # strictly increasing


def test_reference_points():
    assert latency_ms(60, 100, 0) == pytest.approx(125.0)  # 50 / (1 - 0.6)
    assert latency_ms(95, 100, 0) == pytest.approx(1000.0)  # 50 / 0.05
    assert latency_ms(500, 100, 0) == pytest.approx(1000.0)  # rho capped at 0.95


def test_backlog_adds_latency():
    assert latency_ms(50, 100, backlog=20) > latency_ms(50, 100, backlog=0)


def test_latency_is_capped():
    cfg = SimConfig()
    assert latency_ms(500, 100, backlog=1e6) == cfg.max_latency_ms
    assert latency_ms(50, 0, 0) == cfg.max_latency_ms  # no capacity at all


def test_sla_reachable_at_hpa_target():
    # At HPA's 60% target, latency should be comfortably under the 300 ms SLA.
    assert latency_ms(60, 100, 0) < SimConfig().sla_latency_ms
