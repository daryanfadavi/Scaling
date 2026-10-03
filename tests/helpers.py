"""Small builders shared by the tests."""

from __future__ import annotations

from controller.base import Observation
from simulator.scenario import OperatorNote, Scenario

NO_NOISE = {"load_noise_pct": 0.0, "latency_noise_std": 0.0}


def constant_scenario(rate: float, ticks: int = 10, notes=None, **env) -> Scenario:
    """A flat-load scenario with noise switched off, so numbers are exact."""
    return Scenario(
        name="test",
        load={"type": "constant", "rate": rate},
        ticks=ticks,
        operator_notes=notes or [],
        env={**NO_NOISE, **env},
    )


def make_obs(
    replicas_ready: int = 3, cpu_pct: float = 50.0, tick: int = 0, **overrides
) -> Observation:
    """An Observation with sensible defaults; override any field by keyword."""
    fields = {
        "tick": tick,
        "time_s": tick * 15.0,
        "replicas_ready": replicas_ready,
        "replicas_pending": 0,
        "cpu_pct": cpu_pct,
        "request_rate": replicas_ready * 50 * cpu_pct / 100,
        "latency_ms": 100.0,
        "backlog": 0.0,
        "min_replicas": 1,
        "max_replicas": 20,
        "operator_notes": [],
        "history": [],
    }
    fields.update(overrides)
    return Observation(**fields)


__all__ = ["NO_NOISE", "OperatorNote", "constant_scenario", "make_obs"]
