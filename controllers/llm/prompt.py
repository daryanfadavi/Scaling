"""What the model is told: the system prompt and how one snapshot becomes the user message.

Everything the model sees comes from a telemetry snapshot (docs/telemetry.md), the
replica bounds, and the controller's own recent decisions. Nothing about the
experiment (scenario, planned load, which controller is being compared) goes in here:
that would break the fairness rule in controllers/README.md.

Change anything below -> bump PROMPT_VERSION, so results say which prompt produced them.
"""

from __future__ import annotations

PROMPT_VERSION = "cluster-v1"

# The service-level goal. Same number the simulator uses (docs/onboarding/camp_qmind.md),
# applied to the app's server-side p95 latency. Agree on it with the team before final runs.
LATENCY_TARGET_MS = 300

SYSTEM_PROMPT = f"""\
You are the autoscaler for one web service running on Kubernetes. Every 15 seconds \
you receive its current telemetry and decide whether to add replicas (pods), remove \
replicas, or leave the count unchanged.

GOAL
Keep the 95th-percentile request latency under {LATENCY_TARGET_MS} ms and keep the error \
rate near zero, while running as few replicas as you can. Both matter: a slow or failing \
service is a bad outcome, and so is paying for pods that are not needed.

HOW THE SERVICE WORKS
- Every request does the same fixed amount of CPU work, so request rate and CPU \
utilization move together.
- Each pod requests 100m of CPU and may burst up to 500m. "CPU" in the telemetry is CPU \
used divided by CPU requested, so it can exceed 100% (up to about 500%). Latency stays low \
while pods have spare CPU and rises steeply once they are saturated.
- Requests are spread evenly across the pods that are ready.
- A new pod serves no traffic until it is ready. Pods that exist but are not ready are \
counted as "starting". Capacity you request now is not available immediately, so if \
several pods are already starting, wait for them before adding more.
- Rates, latency and CPU are averaged over the last minute, so they trail what is \
happening right now. A change you made a moment ago may not be visible yet.
- Removing a pod takes effect quickly. Do not remove pods right after adding them, or \
while latency is close to the limit.

TELEMETRY FIELDS
- replicas: the count the Deployment currently asks for, how many pods are ready, how \
many are starting, and the allowed minimum and maximum.
- cpu: percent of the CPU request, summed over running pods.
- request rate: requests per second reaching the service, all pods together.
- latency p50 / p95: server-side latency in milliseconds. "n/a" means no requests arrived \
in the last minute, so there is nothing to measure.
- error rate: fraction of requests answered with a 5xx error. "n/a" means no requests.
- Any field can be "n/a" if no data was available. Do not guess what it was.

ANSWER FORMAT
Reply with exactly one JSON object and nothing else: no code fences and no text before \
or after it.

{{"action": "SCALE_UP", "amount": 2, "reason": "one short sentence"}}

- "action" must be exactly one of "SCALE_UP", "SCALE_DOWN" or "NO_CHANGE".
- "amount" is how many replicas to add or remove: the integer 1, 2 or 3. Use 0 with NO_CHANGE.
- "reason" is one short sentence explaining the decision.
Your answer is checked before anything is applied: replica bounds and cooldowns are \
enforced outside you, and an answer in any other format is ignored (nothing changes).
"""


def _fmt(value, spec: str, unit: str = "") -> str:
    """Format a number, or 'n/a' for missing data (None)."""
    return "n/a" if value is None else f"{value:{spec}}{unit}"


def build_prompt(
    snapshot: dict,
    current_replicas: int,
    min_replicas: int,
    max_replicas: int,
    history: list[dict],
) -> str:
    """One decision's user message.

    snapshot:         from telemetry.latest_snapshot()
    current_replicas: what the Deployment asks for right now (read from Kubernetes,
                      not from the snapshot, which can still show the previous value)
    history:          this controller's recent ticks, oldest first; each entry has
                      the snapshot fields plus "action" (what it did that tick)
    """
    lines = [
        f"Replicas: {current_replicas} requested, "
        f"{_fmt(snapshot.get('replicas_ready'), '.0f')} ready, "
        f"{_fmt(snapshot.get('replicas_starting'), '.0f')} starting "
        f"(min {min_replicas}, max {max_replicas})",
        f"CPU: {_fmt(snapshot.get('cpu_utilization_pct'), '.0f', '%')}   "
        f"Request rate: {_fmt(snapshot.get('request_rate_rps'), '.1f', ' req/s')}",
        f"Latency: p50 {_fmt(snapshot.get('latency_p50_ms'), '.0f', ' ms')}, "
        f"p95 {_fmt(snapshot.get('latency_p95_ms'), '.0f', ' ms')} "
        f"(limit {LATENCY_TARGET_MS} ms)   "
        f"Errors: {_fmt(snapshot.get('error_rate'), '.1%')}",
    ]

    if history:
        lines += ["", "Your recent decisions (oldest first, 15 s apart):",
                  "ticks ago | requested | ready | starting | cpu% | req/s | p95 ms | errors | you did"]
        for age, past in zip(range(len(history), 0, -1), history):
            lines.append(
                f"{age} | {_fmt(past.get('replicas_requested'), '.0f')} | "
                f"{_fmt(past.get('replicas_ready'), '.0f')} | "
                f"{_fmt(past.get('replicas_starting'), '.0f')} | "
                f"{_fmt(past.get('cpu_utilization_pct'), '.0f')} | "
                f"{_fmt(past.get('request_rate_rps'), '.1f')} | "
                f"{_fmt(past.get('latency_p95_ms'), '.0f')} | "
                f"{_fmt(past.get('error_rate'), '.1%')} | "
                f"{past.get('action', '?')}"
            )

    lines += ["", "Decide this tick's action. Reply with only the JSON object."]
    return "\n".join(lines)
