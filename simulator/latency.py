"""Latency model: how slow requests get as the replicas fill up.

Two effects, added together:

1. Queueing delay. Like a checkout line: at low utilization requests are
   served almost immediately; as utilization (rho = load / capacity) gets
   close to 100%, waiting time explodes. The classic M/M/1-style shape is
   base / (1 - rho). We cap rho at 0.95 so the formula doesn't divide by zero.

       rho = 0.2  ->   63 ms
       rho = 0.6  ->  125 ms
       rho = 0.8  ->  250 ms
       rho = 0.95 -> 1000 ms

2. Backlog wait. If requests are already queued from earlier ticks, a new
   request waits behind them: backlog / capacity seconds.

The total gets a little multiplicative noise and is capped at max_latency_ms.
"""

from __future__ import annotations

from simulator.config import SimConfig


def latency_ms(
    load_rate: float,
    capacity_rate: float,
    backlog: float,
    config: SimConfig | None = None,
    noise: float = 0.0,
) -> float:
    """Average request latency for one tick.

    Args:
        load_rate: Incoming requests per second.
        capacity_rate: Requests per second the ready replicas can serve.
        backlog: Requests left queued at the end of the tick.
        config: Simulator constants (base latency, caps).
        noise: Multiplicative noise, e.g. 0.02 means +2%.
    """
    cfg = config or SimConfig()
    if capacity_rate <= 0:
        return cfg.max_latency_ms

    rho = min(load_rate / capacity_rate, cfg.rho_cap)
    queueing_ms = cfg.base_latency_ms / (1 - rho)
    backlog_wait_ms = 1000 * backlog / capacity_rate

    total = (queueing_ms + backlog_wait_ms) * (1 + noise)
    return min(total, cfg.max_latency_ms)
