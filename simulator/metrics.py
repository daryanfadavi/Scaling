"""Turn per-tick records into one summary per run.

These are the numbers on the leaderboard. Lower is better for all of them,
but they trade off: you can always buy fewer SLA violations with more
replica-minutes (i.e. more money). A good controller gets both low.
"""

from __future__ import annotations

from typing import Any

import numpy as np


def summarize(records: list[dict[str, Any]], tick_seconds: float) -> dict[str, Any]:
    latency = np.array([r["latency_ms"] for r in records])
    llm_latencies = [
        r["llm_latency_s"]
        for r in records
        if r["llm_called"] and r["llm_latency_s"] is not None
    ]

    return {
        "ticks": len(records),
        # Reliability: how many 15-second ticks had latency above the SLA.
        "sla_violation_ticks": int(sum(r["sla_violated"] for r in records)),
        "mean_latency_ms": round(float(latency.mean()), 1),
        "p95_latency_ms": round(float(np.percentile(latency, 95)), 1),
        "max_backlog": round(max(r["backlog"] for r in records), 1),
        # Cost: replicas you pay for x minutes. Pending replicas count too --
        # in a real cloud you're billed from the moment the machine starts booting.
        "replica_minutes": round(
            sum(r["replicas_ready"] + r["replicas_pending"] for r in records)
            * tick_seconds
            / 60,
            2,
        ),
        # Behaviour
        "scaling_actions": int(sum(r["scaled"] for r in records)),
        "clamped_decisions": int(sum(bool(r["clamp_note"]) for r in records)),
        "invalid_decisions": int(sum(r["invalid"] for r in records)),
        # LLM usage
        "llm_calls": int(sum(r["llm_called"] for r in records)),
        "llm_latency_total_s": round(float(sum(llm_latencies)), 3),
        "llm_latency_mean_s": (
            round(float(np.mean(llm_latencies)), 3) if llm_latencies else None
        ),
    }
