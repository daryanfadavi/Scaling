"""The LLM autoscaler for the cluster experiments (a separate process).

    python -m controllers.llm.main        # normally started by controllers/llm/start.sh

Every DECISION_INTERVAL_S seconds:

    observe  ->  ask the model  ->  parse + validate  ->  guardrails  ->  kubectl scale

* observe:    telemetry.latest_snapshot() (docs/telemetry.md). No PromQL of our own.
* ask:        one prompt (controllers/llm/prompt.py) to the model through controller/llm_client.py.
* validate:   controller.llm_controller.parse_response (the same parser as the simulator
              controller). Anything unusable becomes NO_CHANGE.
* guardrails: apply_guardrails() below: replica bounds and cooldowns. The model's output
              never reaches Kubernetes except through this function (AGENTS.md).
* act:        `kubectl scale`, only when the guarded target differs from the current count.

Every tick prints one JSON line to stdout (the runner saves it as controller.log).
SIGTERM or SIGINT stops the loop; a decision made after that is never applied.

This file must not read experiment configs, scenario files or results: the controller
must not know it is being evaluated (controllers/README.md, rule 4).
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

from dotenv import load_dotenv

from controller.llm_client import LLMClient
from controller.llm_controller import ParsedAction, fallback, parse_response
from controller.openrouter_client import OpenRouterClient
from controllers.llm.prompt import DEFAULT_HISTORY_TICKS, DEFAULT_VARIANT, did_text, get_variant, history_entry
from telemetry import INTERVAL_SECONDS, latest_snapshot

# ---- Settings (fixed: every run uses the same ones; document changes in README.md) ----
DECISION_INTERVAL_S = INTERVAL_SECONDS  # one decision per telemetry snapshot (15 s)
COOLDOWN_AFTER_SCALE_S = 30       # after any scaling action, hold for this long
SCALE_DOWN_COOLDOWN_S = 120       # after any scaling action, don't scale DOWN for this long
HISTORY_TICKS = DEFAULT_HISTORY_TICKS  # past decisions shown to the model (a prompt variant may change it)
KUBECTL_TIMEOUT_S = 30
# ----------------------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]

# Read from the environment given by the runner (controllers/README.md).
KUBE_CONTEXT = os.getenv("KUBE_CONTEXT", "scale")
NAMESPACE = os.getenv("SCALE_NAMESPACE", "scale")
DEPLOYMENT = os.getenv("SCALE_DEPLOYMENT", "scale-app")
MIN_REPLICAS = int(os.getenv("SCALE_MIN_REPLICAS", "1"))
MAX_REPLICAS = int(os.getenv("SCALE_MAX_REPLICAS", "10"))


# ---- Guardrails (pure functions: easy to test, no cluster needed) -------------------------

def apply_guardrails(
    parsed: ParsedAction,
    current_replicas: int,
    now: float,
    last_scale_time: float | None,
    min_replicas: int = MIN_REPLICAS,
    max_replicas: int = MAX_REPLICAS,
) -> tuple[int, str]:
    """Turn a validated action into the replica count we will actually apply.

    Returns (target_replicas, note). `note` says which guardrail changed the
    outcome, or "" if none did. `last_scale_time` is when we last changed the
    replica count (same clock as `now`), or None if we never have.
    """
    if parsed.action == "NO_CHANGE":
        return current_replicas, ""

    since_scale = None if last_scale_time is None else now - last_scale_time
    if since_scale is not None and since_scale < COOLDOWN_AFTER_SCALE_S:
        return current_replicas, f"cooldown: scaled {since_scale:.0f}s ago (hold {COOLDOWN_AFTER_SCALE_S}s)"
    if (parsed.action == "SCALE_DOWN" and since_scale is not None
            and since_scale < SCALE_DOWN_COOLDOWN_S):
        return current_replicas, (
            f"scale-down cooldown: scaled {since_scale:.0f}s ago (hold {SCALE_DOWN_COOLDOWN_S}s)")

    wanted = current_replicas + (parsed.amount if parsed.action == "SCALE_UP" else -parsed.amount)
    target = max(min_replicas, min(max_replicas, wanted))
    note = f"clamped {wanted} to {target} (bounds {min_replicas}-{max_replicas})" if target != wanted else ""
    return target, note


# ---- Kubernetes -----------------------------------------------------------------------------

def kubectl(*args: str) -> str:
    result = subprocess.run(
        ["kubectl", "--context", KUBE_CONTEXT, "-n", NAMESPACE, *args],
        capture_output=True, text=True, timeout=KUBECTL_TIMEOUT_S,
    )
    if result.returncode != 0:
        raise RuntimeError(f"kubectl {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def read_current_replicas() -> int:
    """What the Deployment asks for right now. Read from Kubernetes (not from the
    snapshot) because a snapshot can still show the value from before our last scale."""
    return int(kubectl("get", "deployment", DEPLOYMENT, "-o", "jsonpath={.spec.replicas}"))


def scale_to(target: int) -> None:
    # Last line of defence: the guardrails already clamp, but this is the only
    # place that touches the Kubernetes API, so check again here.
    if not MIN_REPLICAS <= target <= MAX_REPLICAS:
        raise ValueError(f"refusing to scale to {target}: outside {MIN_REPLICAS}-{MAX_REPLICAS}")
    kubectl("scale", "deployment", DEPLOYMENT, f"--replicas={target}")


# ---- The model ------------------------------------------------------------------------------

def make_client():
    """Anthropic by default; SCALE_PROVIDER=openrouter for OpenRouter (keys and model
    names come from the environment or .env, see .env.example). No response cache:
    every decision is a live call."""
    if os.getenv("SCALE_PROVIDER", "anthropic").lower() == "openrouter":
        return OpenRouterClient()
    # temperature=None: the Anthropic API now rejects the temperature parameter for
    # claude-haiku-4-5 ("Extra inputs are not permitted"), which made every call fail.
    # The value in use is logged in the start line of controller.log.
    return LLMClient(temperature=None)


# ---- One decision ---------------------------------------------------------------------------

class Controller:
    def __init__(self, client, stop: threading.Event, clock=time.monotonic, variant=None):
        self.client = client
        self.stop = stop
        self.clock = clock
        self.variant = variant or get_variant(DEFAULT_VARIANT)  # which prompt (controllers/llm/prompt.py)
        self.history: deque[dict] = deque(maxlen=self.variant.history_ticks)
        self.last_scale_time: float | None = None
        self.tick = 0

    def step(self) -> dict:
        """Run one observe -> decide -> act cycle and return what to log."""
        record: dict = {"tick": self.tick, "prompt_version": self.variant.name}
        self.tick += 1
        try:
            snapshot = latest_snapshot()
            current = read_current_replicas()
        except Exception as error:  # Prometheus or kubectl hiccup: hold and try next tick
            record.update(action="NO_CHANGE", applied=False, error=f"observe failed: {error}")
            return record

        record["snapshot"] = {k: snapshot.get(k) for k in (
            "replicas_ready", "replicas_starting", "cpu_utilization_pct", "request_rate_rps",
            "latency_p50_ms", "latency_p95_ms", "error_rate")}
        record["replicas_before"] = current

        prompt = self.variant.build(snapshot, current, MIN_REPLICAS, MAX_REPLICAS, list(self.history))
        response = self.client.complete(self.variant.system_prompt, prompt, use_cache=False)
        if response is None:
            parsed = fallback("LLM call failed")
        else:
            parsed = parse_response(response.text)
            record["llm_latency_s"] = round(response.latency_s, 2)
        record.update(model=getattr(self.client, "model", None), llm_raw=response.text if response else "",
                      invalid=parsed.invalid, reason=parsed.reason)

        now = self.clock()
        target, note = apply_guardrails(parsed, current, now, self.last_scale_time)
        record.update(action=parsed.action, amount=parsed.amount, guardrail=note, replicas_target=target)

        applied = False
        if target != current and self.stop.is_set():
            record["guardrail"] = "stopping: decision not applied"
        elif target != current:
            try:
                scale_to(target)
                self.last_scale_time = now
                applied = True
            except Exception as error:
                record["error"] = f"scale failed: {error}"
        record["applied"] = applied

        # What the model is told it did: the asked-for action, and whether it took effect.
        did = did_text(parsed.action, parsed.amount, parsed.invalid, applied)
        self.history.append(history_entry(snapshot, target if applied else current, did))
        return record


def log_line(record: dict) -> None:
    print(json.dumps({"time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **record}), flush=True)


def main() -> None:
    load_dotenv(REPO_ROOT / ".env")  # API key; the runner also passes its own environment
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())

    try:  # which prompt: SCALE_PROMPT, set by the plugin folder (controllers/llm_trend, ...)
        variant = get_variant(os.getenv("SCALE_PROMPT") or DEFAULT_VARIANT)
    except ValueError as error:
        sys.exit(str(error))
    client = make_client()
    controller = Controller(client, stop, variant=variant)
    log_line({"event": "start", "model": getattr(client, "model", None),
              "temperature": getattr(client, "temperature", None), "prompt_version": variant.name,
              "history_ticks": controller.history.maxlen,
              "interval_s": DECISION_INTERVAL_S, "cooldown_after_scale_s": COOLDOWN_AFTER_SCALE_S,
              "scale_down_cooldown_s": SCALE_DOWN_COOLDOWN_S, "replica_bounds": [MIN_REPLICAS, MAX_REPLICAS]})

    next_tick = time.monotonic()
    while not stop.is_set():
        log_line(controller.step())
        next_tick += DECISION_INTERVAL_S
        # If a tick overran (slow model), skip missed ticks instead of firing back-to-back.
        next_tick = max(next_tick, time.monotonic())
        stop.wait(next_tick - time.monotonic())
    log_line({"event": "stopped"})


if __name__ == "__main__":
    sys.exit(main())
