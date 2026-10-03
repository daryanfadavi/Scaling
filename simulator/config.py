"""All the simulator's physical constants in one place.

These are deliberately simple, round numbers. They are NOT calibrated against
a real cluster -- the simulator is a pilot for comparing controllers under
identical conditions, not a model of production Kubernetes.

A scenario YAML can override any of these under an `env:` key, e.g.

    env:
      initial_replicas: 4
"""

from __future__ import annotations

from dataclasses import dataclass, fields, replace
from typing import Any


@dataclass(frozen=True)
class SimConfig:
    # --- time ---
    tick_seconds: float = 15.0  # 1 tick = 15 simulated seconds

    # --- capacity ---
    capacity_per_replica: float = 50.0  # req/s one ready replica serves at 100% CPU

    # --- replicas ---
    initial_replicas: int = 2
    min_replicas: int = 1
    max_replicas: int = 20
    max_step: int = 4  # max change in replica count per tick (env guardrail)

    # New replicas need this many full ticks to start before they serve traffic.
    # See the long comment in simulator/env.py for why this matters so much.
    startup_delay_ticks: int = 2

    # --- latency model (see simulator/latency.py) ---
    base_latency_ms: float = 50.0
    # utilization above rho_cap is treated as rho_cap in the queueing term
    rho_cap: float = 0.95
    max_latency_ms: float = 2000.0
    latency_noise_std: float = 0.03  # +/-3% (1 std dev) multiplicative noise

    # --- SLA ---
    sla_latency_ms: float = 300.0  # a tick violates the SLA if latency > this

    # --- workload noise ---
    load_noise_pct: float = 5.0  # load is multiplied by uniform(1-5%, 1+5%)

    # --- observation ---
    history_length: int = 5  # how many past ticks controllers see in obs.history

    def with_overrides(self, overrides: dict[str, Any] | None) -> SimConfig:
        """Return a copy with some fields changed (used for YAML `env:` overrides)."""
        if not overrides:
            return self
        known = {f.name for f in fields(self)}
        unknown = set(overrides) - known
        if unknown:
            raise ValueError(f"Unknown env setting(s) in scenario: {sorted(unknown)}")
        return replace(self, **overrides)
