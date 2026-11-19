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
- Each pod requests 100m of CPU and may burst up to 200m. "CPU" in the telemetry is CPU \
used divided by CPU requested, so it can exceed 100% (up to about 200%). Latency stays low \
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


# ============================================================================================
# Prompt variants, for prompt experiments
#
# Each variant changes ONE thing from the baseline "cluster-v1", so a difference in results can
# be traced to that change. Choose one with SCALE_PROMPT=<name> (the plugin folders
# controllers/llm_trend, llm_words and llm_nohist just set it), or compare them offline with
# experiments/prompt_lab.py.
#
#   cluster-v1          the baseline above (unchanged)
#   cluster-v1-nohist   same prompt, but the model sees no past decisions (SAIR-style ablation)
#   cluster-v2-trend    adds a "Trend" line computed from the controller's own recent readings
#   cluster-v2-words    adds a word (low / high / ...) next to CPU, latency and errors (ORACL-style)
#   cluster-v1-hist10 / -hist15   same prompt, but the last 10 / 15 decisions (SAIR-style diary length)
#   cluster-v2-outcomes adds what each past decision led to by the next reading (SAIR-style outcomes)
#
# Rules: nothing here may mention the experiment, the scenario or the other controllers, and any
# new variant needs a new name so results say which prompt produced them.
# ============================================================================================

from dataclasses import dataclass  # noqa: E402

DEFAULT_VARIANT = "cluster-v1"
DEFAULT_HISTORY_TICKS = 5   # past decisions the baseline shows; keep equal to HISTORY_TICKS in main.py

HISTORY_FIELDS = ("replicas_ready", "replicas_starting", "cpu_utilization_pct",
                  "request_rate_rps", "latency_p95_ms", "error_rate")


def did_text(action: str, amount: int, invalid: bool, applied: bool) -> str:
    """What the model is told it did on a past tick: what it asked for, and whether it took effect."""
    asked = "NO_CHANGE" if action == "NO_CHANGE" else f"{action} {amount}"
    if invalid:
        return "invalid answer, nothing changed"
    if applied or action == "NO_CHANGE":
        return asked
    return f"{asked} (blocked by guardrails, nothing changed)"


def history_entry(snapshot: dict, replicas_requested: int, did: str) -> dict:
    """One past tick as shown in the 'recent decisions' table."""
    return {**{k: snapshot.get(k) for k in HISTORY_FIELDS},
            "replicas_requested": replicas_requested, "action": did}


# ---- Trend line (variant cluster-v2-trend) -------------------------------------------------

TREND_READINGS = 4  # this tick plus the 3 before it (45 s)
# label, snapshot key, number format, unit, "flat" if the change is no bigger than this
TREND_FIELDS = (
    ("request rate", "request_rate_rps", ".1f", " req/s", 1.0),
    ("CPU", "cpu_utilization_pct", ".0f", "%", 5.0),
    ("p95 latency", "latency_p95_ms", ".0f", " ms", 5.0),
)


def _direction(old, new, floor: float, rel: float = 0.10) -> str:
    """rising / falling / flat. A change counts only if it exceeds both `floor` and `rel` of the values."""
    if old is None or new is None:
        return "n/a"
    change = new - old
    if abs(change) <= max(floor, rel * max(abs(old), abs(new))):
        return "flat"
    return "rising" if change > 0 else "falling"


def trend_line(snapshot: dict, history: list[dict]) -> str | None:
    """First vs latest value over the last few readings, from what the controller already saw."""
    readings = list(history[-(TREND_READINGS - 1):]) + [snapshot]
    if len(readings) < 2:
        return None
    parts = []
    for label, key, spec, unit, floor in TREND_FIELDS:
        first, last = readings[0].get(key), readings[-1].get(key)
        parts.append(f"{label} {_fmt(first, spec, unit)} -> {_fmt(last, spec, unit)} "
                     f"({_direction(first, last, floor)})")
    return f"Trend over the last {len(readings)} readings (oldest to newest): " + ", ".join(parts)


# ---- Words next to numbers (variant cluster-v2-words) --------------------------------------
# These thresholds are a first guess, to be reviewed by the team. CPU is relative to the 100m request.

def _cpu_word(value) -> str:
    if value is None:
        return "n/a"
    return "low" if value < 40 else "moderate" if value < 100 else "high" if value < 200 else "very high"


def _latency_word(value) -> str:
    if value is None:
        return "n/a"
    share = value / LATENCY_TARGET_MS
    return "low" if share < 0.25 else "moderate" if share < 0.7 else "near the limit" if share <= 1 else "over the limit"


def _error_word(value) -> str:
    if value is None:
        return "n/a"
    return "none" if value <= 0.001 else "low" if value < 0.01 else "high"


def _outcome_text(row: dict, nxt: dict) -> str:
    """What happened by the next reading after a past decision: pods ready, CPU and p95."""
    parts = []
    ready0, ready1 = row.get("replicas_ready"), nxt.get("replicas_ready")
    if ready0 is not None and ready1 is not None and ready0 != ready1:
        parts.append(f"pods ready {ready0:.0f}->{ready1:.0f}")
    for label, key, unit in (("CPU", "cpu_utilization_pct", "%"), ("p95", "latency_p95_ms", " ms")):
        before, after = row.get(key), nxt.get(key)
        if before is not None and after is not None:
            parts.append(f"{label} {before:.0f}->{after:.0f}{unit}")
    return ", ".join(parts) if parts else "n/a"


def _build_v2(snapshot, current_replicas, min_replicas, max_replicas, history, *,
              words=False, trend=False, outcomes=False) -> str:
    """Same message as build_prompt, plus the optional additions. With every flag off the output is
    identical to build_prompt (a test checks this), so variants differ only by what they add."""
    def tag(word):  # " (high)" or nothing
        return f" ({word})" if words and word != "n/a" else ""

    lines = [
        f"Replicas: {current_replicas} requested, "
        f"{_fmt(snapshot.get('replicas_ready'), '.0f')} ready, "
        f"{_fmt(snapshot.get('replicas_starting'), '.0f')} starting "
        f"(min {min_replicas}, max {max_replicas})",
        f"CPU: {_fmt(snapshot.get('cpu_utilization_pct'), '.0f', '%')}{tag(_cpu_word(snapshot.get('cpu_utilization_pct')))}   "
        f"Request rate: {_fmt(snapshot.get('request_rate_rps'), '.1f', ' req/s')}",
        f"Latency: p50 {_fmt(snapshot.get('latency_p50_ms'), '.0f', ' ms')}, "
        f"p95 {_fmt(snapshot.get('latency_p95_ms'), '.0f', ' ms')}{tag(_latency_word(snapshot.get('latency_p95_ms')))} "
        f"(limit {LATENCY_TARGET_MS} ms)   "
        f"Errors: {_fmt(snapshot.get('error_rate'), '.1%')}{tag(_error_word(snapshot.get('error_rate')))}",
    ]
    if trend:
        line = trend_line(snapshot, history)
        if line:
            lines.append(line)

    if history:
        header = "ticks ago | requested | ready | starting | cpu% | req/s | p95 ms | errors | you did"
        lines += ["", "Your recent decisions (oldest first, 15 s apart):",
                  header + (" | then (by the next reading)" if outcomes else "")]
        following = list(history[1:]) + [snapshot]   # the reading that came after each past decision
        for age, past, nxt in zip(range(len(history), 0, -1), history, following):
            row = (f"{age} | {_fmt(past.get('replicas_requested'), '.0f')} | "
                   f"{_fmt(past.get('replicas_ready'), '.0f')} | "
                   f"{_fmt(past.get('replicas_starting'), '.0f')} | "
                   f"{_fmt(past.get('cpu_utilization_pct'), '.0f')} | "
                   f"{_fmt(past.get('request_rate_rps'), '.1f')} | "
                   f"{_fmt(past.get('latency_p95_ms'), '.0f')} | "
                   f"{_fmt(past.get('error_rate'), '.1%')} | "
                   f"{past.get('action', '?')}")
            lines.append(row + (f" | {_outcome_text(past, nxt)}" if outcomes else ""))

    lines += ["", "Decide this tick's action. Reply with only the JSON object."]
    return "\n".join(lines)


def _system_with(note: str) -> str:
    """The baseline system prompt with one extra line added to the telemetry-field list."""
    marker = "\n\nANSWER FORMAT"
    assert SYSTEM_PROMPT.count(marker) == 1
    return SYSTEM_PROMPT.replace(marker, "\n" + note + marker, 1)


TREND_NOTE = (
    "- trend: how request rate, CPU and p95 latency changed over the last few readings, oldest to newest. "
    "These are the same one-minute averages, so they lag: a falling trend means the load is already "
    "lower than the level shown."
)
WORDS_NOTE = (
    "- Some numbers are followed by a word in brackets (low, moderate, high, ...) giving a rough reading of "
    "that number. The numbers are what count; the words are only a guide."
)


OUTCOMES_NOTE = (
    "- the last column of the recent-decisions table, 'then', shows how pods, CPU and p95 latency changed by the next "
    "reading (15 s later), so you can see what each past decision led to."
)


@dataclass(frozen=True)
class PromptVariant:
    name: str                      # recorded as prompt_version in controller.log
    description: str
    system_prompt: str
    history_ticks: int = DEFAULT_HISTORY_TICKS   # past decisions shown (0 = none)
    words: bool = False
    trend: bool = False
    outcomes: bool = False

    def build(self, snapshot, current_replicas, min_replicas, max_replicas, history) -> str:
        history = history[-self.history_ticks:] if self.history_ticks else []
        return _build_v2(snapshot, current_replicas, min_replicas, max_replicas, history,
                         words=self.words, trend=self.trend, outcomes=self.outcomes)


VARIANTS = {v.name: v for v in (
    PromptVariant("cluster-v1", "The baseline prompt.", SYSTEM_PROMPT),
    PromptVariant("cluster-v1-nohist", "Baseline, but the model sees no past decisions.", SYSTEM_PROMPT,
                  history_ticks=0),
    PromptVariant("cluster-v1-hist10", "Baseline, but the model sees the last 10 decisions.", SYSTEM_PROMPT,
                  history_ticks=10),
    PromptVariant("cluster-v1-hist15", "Baseline, but the model sees the last 15 decisions (SAIR-length diary).",
                  SYSTEM_PROMPT, history_ticks=15),
    PromptVariant("cluster-v2-outcomes", "Baseline plus what each past decision led to by the next reading.",
                  _system_with(OUTCOMES_NOTE), outcomes=True),
    PromptVariant("cluster-v2-trend", "Baseline plus a computed trend line.", _system_with(TREND_NOTE),
                  trend=True),
    PromptVariant("cluster-v2-words", "Baseline plus a word next to CPU, latency and errors.",
                  _system_with(WORDS_NOTE), words=True),
)}


def get_variant(name: str) -> PromptVariant:
    try:
        return VARIANTS[name]
    except KeyError:
        raise ValueError(f"unknown prompt variant {name!r}; choose one of: {', '.join(VARIANTS)}") from None
