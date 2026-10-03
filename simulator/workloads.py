"""Workload shapes: how many requests per second arrive at each tick.

Each shape is a plain function returning a numpy array of length `ticks`.
To add a new shape (bursty, periodic, ...), write a function here and add it
to WORKLOADS. Then use it from a scenario YAML with `load: {type: <name>, ...}`.
"""

from __future__ import annotations

from typing import Any

import numpy as np


def constant(ticks: int, rate: float) -> np.ndarray:
    """The same load every tick."""
    return np.full(ticks, float(rate))


def ramp(
    ticks: int, start: float, end: float, ramp_start_tick: int, ramp_end_tick: int
) -> np.ndarray:
    """`start` until ramp_start_tick, then a straight line up to `end` at
    ramp_end_tick, then hold at `end`."""
    t = np.arange(ticks)
    return np.interp(t, [ramp_start_tick, ramp_end_tick], [start, end]).astype(float)


def spike(
    ticks: int, base: float, peak: float, start_tick: int, duration_ticks: int
) -> np.ndarray:
    """`base` load, jumping instantly to `peak` for `duration_ticks`, then back."""
    load = np.full(ticks, float(base))
    load[start_tick : start_tick + duration_ticks] = peak
    return load


WORKLOADS = {
    "constant": constant,
    "ramp": ramp,
    "spike": spike,
}


def base_trace(load_cfg: dict[str, Any], ticks: int) -> np.ndarray:
    """Noise-free load trace from a scenario's `load:` section."""
    params = dict(load_cfg)
    kind = params.pop("type")
    if kind not in WORKLOADS:
        raise ValueError(
            f"Unknown workload type {kind!r}; choose from {sorted(WORKLOADS)}"
        )
    return WORKLOADS[kind](ticks, **params)


def noisy_trace(
    load_cfg: dict[str, Any], ticks: int, noise_pct: float, rng: np.random.Generator
) -> np.ndarray:
    """Load trace with +/-noise_pct uniform multiplicative noise.

    The noise comes only from `rng` (seeded from the run's seed), so the same
    seed gives the exact same trace no matter which controller is running.
    """
    noise = rng.uniform(-noise_pct / 100, noise_pct / 100, size=ticks)
    return base_trace(load_cfg, ticks) * (1 + noise)
