"""LLM controller: telemetry (and optionally operator notes) in, JSON action out.

>>> PARTLY A STUB -- Pair B implements the marked parts at Camp QMIND. <<<
Definition of done: `pytest -m todo tests/todo/test_llm_parser.py` passes, and
`python -m experiments.run --controller llm_context --scenario spike_with_warning`
runs end to end with your API key.

One class, three experimental variants (selected with flags):

    LLMController(client, use_context=False)  -> "llm"          metric-only
    LLMController(client, use_context=True)   -> "llm_context"  + operator notes
    ...use_history=False                      -> "..._nohist"   no recent history

What's already done (don't need to touch):
  * decide(): the whole per-tick loop -- filter obs, build prompt, call the
    API through LLMClient, parse, convert the action to a replica target,
    fall back to NO_CHANGE if anything fails, record latency.
  * visible_observation(): removes operator notes (metric-only) and/or
    history before the prompt is built, so the variants are a fair ablation.
    build_prompt never even sees what the variant shouldn't know.

What YOU implement (marked TODO):
  1. SYSTEM_PROMPT  -- the instructions for the model.
  2. build_prompt(obs) -> str  -- render one observation as the user message.
  3. parse_response(text) -> ParsedAction  -- validate the model's reply.
  4. (optional) LLMController.apply_guardrails -- e.g. a cooldown.

SUGGESTED OUTPUT SCHEMA (ask the model for exactly this JSON):

    {"action": "SCALE_UP" | "SCALE_DOWN" | "NO_CHANGE",
     "amount": 1-3,          # ignored for NO_CHANGE
     "reason": "one short sentence"}

FALLBACK RULE: if the reply is not valid (not JSON, unknown action, amount not
an integer in 1..3, ...) the controller does NO_CHANGE and the decision is
marked invalid=True. Invalid decisions are counted on the leaderboard.

Safety: the LLM's answer is never applied directly. It passes through
parse_response (validation), apply_guardrails (controller-level), and then
the environment's own guardrails (min/max replicas, max +/-4 per tick).
"""

from __future__ import annotations
import json
from dataclasses import dataclass, replace

from controller.base import Controller, Decision, Observation
from controller.llm_client import LLMClient

VALID_ACTIONS = ("SCALE_UP", "SCALE_DOWN", "NO_CHANGE")
MIN_AMOUNT = 1
MAX_AMOUNT = 3


@dataclass
class ParsedAction:
    """A validated action from the LLM.

    action: one of VALID_ACTIONS
    amount: replicas to add/remove (1..3); use 0 for NO_CHANGE
    reason: the model's explanation (or why we fell back)
    invalid: True if the reply could not be used and this is a fallback
    """

    action: str
    amount: int
    reason: str = ""
    invalid: bool = False


def fallback(why: str) -> ParsedAction:
    """The safe default when the LLM's output can't be used: hold steady."""
    return ParsedAction(
        action="NO_CHANGE", amount=0, reason=f"fallback: {why}", invalid=True
    )


# --------------------------------------------------------------------------- #
# TODO(pair B): the three pieces below are yours.
# --------------------------------------------------------------------------- #

# TODO(pair B): write the system prompt. Things the model needs to know:
#   - its job (keep latency under 300 ms while not wasting replicas)
#   - how the system works: each replica serves ~50 req/s at 100% CPU, new
#     replicas take ~30 s (2 ticks) to start, replicas are bounded 1..20
#   - the exact JSON format to answer with, and to answer with ONLY that JSON
#   - (for the context variant) that operator notes may announce future events
# Keep it identical across variants -- only the observation should differ.
SYSTEM_PROMPT = """\
You are an autoscaler for a web service. Every 15 seconds (one tick) you receive \
the service's current telemetry and decide whether to add replicas, remove \
replicas, or leave the count unchanged.

GOAL
Keep average request latency under 300 ms (the SLA) while running as few \
replicas as you can. Both matter: every tick above 300 ms is an SLA violation, \
and every replica costs money for as long as it runs.

HOW THE SYSTEM WORKS
- Each ready replica can serve about 50 requests per second at 100% CPU. CPU is \
the average utilization of the ready replicas.
- Latency is low while there is spare capacity and rises steeply as CPU \
approaches 100%. Requests that cannot be served queue up as a backlog, which \
adds latency until it is cleared.
- A new replica takes about 30 seconds (2 ticks) to start. "Starting" replicas \
are already on their way but serve no traffic yet. Removing a replica takes \
effect immediately.
- The replica count always stays between the min and max shown in the telemetry.
- You can add or remove at most 3 replicas per decision.
- The telemetry may include recent history and notes from a human operator. \
Operator notes can announce future events, such as an expected change in \
traffic. Each note says when it was posted; times inside a note are relative \
to that moment, so compare it with the current time. Because of the startup \
delay, capacity for an announced increase must be requested before it arrives.

ANSWER FORMAT
Reply with exactly one JSON object and nothing else: no code fences and no text \
before or after it.

{"action": "SCALE_UP", "amount": 2, "reason": "one short sentence"}

- "action" must be exactly one of "SCALE_UP", "SCALE_DOWN" or "NO_CHANGE".
- "amount" is how many replicas to add or remove: the integer 1, 2 or 3. Use 0 \
with NO_CHANGE.
- "reason" is one short sentence explaining the decision.
"""


def build_prompt(obs: Observation) -> str:
    """Render one observation as the user message.

    `obs` has already been filtered by visible_observation(), so:
      * obs.operator_notes is empty for the metric-only variant
      * obs.history is empty when use_history=False
    Just render whatever is there -- if a list is empty, you can skip that section.

    Hints:
      * Plain labelled lines are easier for the model (and you) than raw JSON:
            Current replicas: 3 ready, 1 starting (min 1, max 20)
            CPU: 92%   Request rate: 140 req/s   Latency: 410 ms   Backlog: 0
      * For history, a small table of the last few ticks works well.
      * Include the time (obs.time_s) -- operator notes say things like
        "in ~90 seconds" and are stamped with when they were posted.
      * Never include anything that's not in `obs` (scenario names, the future
        load, ...). That would invalidate the experiment -- see AGENTS.md.
    """
    lines = [
        f"Time: t={obs.time_s:.0f}s (tick {obs.tick})",
        f"Replicas: {obs.replicas_ready} ready, {obs.replicas_pending} starting "
        f"(total {obs.replicas_total}; min {obs.min_replicas}, max {obs.max_replicas})",
        f"CPU: {obs.cpu_pct:.0f}%   Request rate: {obs.request_rate:.0f} req/s   "
        f"Latency: {obs.latency_ms:.0f} ms (SLA limit 300 ms)   Backlog: {obs.backlog:.0f} requests",
    ]
    if obs.history:
        lines.append("")
        lines.append("Recent history (oldest first):")
        lines.append("tick | ready | starting | cpu% | req/s | latency_ms | backlog | target")
        for h in obs.history:
            lines.append(
                f"{h.get('tick', '?')} | {h.get('replicas_ready', '?')} | "
                f"{h.get('replicas_pending', '?')} | {h.get('cpu_pct', '?')} | "
                f"{h.get('request_rate', '?')} | {h.get('latency_ms', '?')} | "
                f"{h.get('backlog', '?')} | {h.get('applied_target', '?')}"
            )

    if obs.operator_notes:
        lines.append("")
        lines.append("Operator notes:")
        for note in obs.operator_notes:
            lines.append(f"- {note}")

    lines.append("")
    lines.append("Decide this tick's action. Reply with only the JSON object.")
    return "\n".join(lines)


def parse_response(text: str) -> ParsedAction:
    """Turn the model's raw reply into a validated ParsedAction.

    Must never raise: for anything unusable, return fallback("<why>").

    Rules (tests/todo/test_llm_parser.py checks these):
      * Valid JSON with a known action and integer amount in 1..3 -> that action.
      * NO_CHANGE -> amount 0 (whatever amount the model sent).
      * Not JSON / missing fields / unknown action -> fallback (NO_CHANGE, invalid).
      * amount outside 1..3, or not an integer, for SCALE_UP/SCALE_DOWN -> fallback.
      * Models sometimes wrap JSON in ```json ... ``` fences or add a sentence
        before it. Handling that gracefully is worth it.

    Hints: `import json`; json.loads raises json.JSONDecodeError on bad input.
    Note that in Python `isinstance(True, int)` is True -- don't accept booleans.
    """
    
    if not isinstance(text, str) or not text.strip():
        return fallback("empty response")

    # 1. Find the JSON object. Try decoding at every '{' and keep the LAST
    #    object that has an "action" key. This handles ```json fences, a
    #    sentence before/after, and models that "think out loud" with
    #    braces before giving the real answer.
    decoder = json.JSONDecoder()
    data = None
    saw_json = False
    pos = text.find("{")
    while pos != -1:
        try:
            obj, _ = decoder.raw_decode(text, pos)
            saw_json = True
            if isinstance(obj, dict) and "action" in obj:
                data = obj
        except ValueError:
            pass
        pos = text.find("{", pos + 1)
    if data is None:
        return fallback("no JSON object with an action" if saw_json else "no valid JSON object found")

    # 2. Check the action.
    action = data.get("action")
    if action not in VALID_ACTIONS:
        return fallback(f"unknown or missing action: {action!r}")

    reason = data.get("reason", "")
    reason = reason if isinstance(reason, str) else str(reason)

    # 3. NO_CHANGE always means amount 0, whatever the model sent.
    if action == "NO_CHANGE":
        return ParsedAction(action="NO_CHANGE", amount=0, reason=reason)

    # 4. SCALE_UP / SCALE_DOWN need a real int in range (bool is a subclass
    #    of int in Python, so `type(...) is int` rejects true/false).
    amount = data.get("amount")
    if type(amount) is not int:
        return fallback(f"amount is not an integer: {amount!r}")
    if not MIN_AMOUNT <= amount <= MAX_AMOUNT:
        return fallback(f"amount out of range: {amount}")

    return ParsedAction(action=action, amount=amount, reason=reason)



# --------------------------------------------------------------------------- #


def action_to_target(parsed: ParsedAction, current_total: int) -> int:
    """Convert a relative action into an absolute replica target."""
    if parsed.action == "SCALE_UP":
        return current_total + parsed.amount
    if parsed.action == "SCALE_DOWN":
        return current_total - parsed.amount
    return current_total


class LLMController(Controller):
    def __init__(
        self, client: LLMClient, use_context: bool = False, use_history: bool = True
    ):
        self.client = client
        self.use_context = use_context
        self.use_history = use_history
        self.name = ("llm_context" if use_context else "llm") + (
            "" if use_history else "_nohist"
        )
        self.reset()

    def reset(self) -> None:
        # TODO(pair B, optional): reset any state you add for apply_guardrails (e.g. last scale tick).
        pass

    def visible_observation(self, obs: Observation) -> Observation:
        """What this variant is allowed to see. Do not weaken this -- it's what
        makes metric-only vs. with-context a fair comparison."""
        return replace(
            obs,
            operator_notes=list(obs.operator_notes) if self.use_context else [],
            history=list(obs.history) if self.use_history else [],
        )

    def apply_guardrails(self, parsed: ParsedAction, obs: Observation) -> ParsedAction:
        """Controller-level guardrails, applied after parsing.

        The environment already clamps to [min, max] replicas and +/-4 per tick
        for every controller. This hook is for LLM-specific safety you might
        want on top, e.g. a cooldown ("no SCALE_DOWN within 4 ticks of a
        SCALE_UP") to stop flapping. If you add one, record why in the reason.

        TODO(pair B, optional): currently a pass-through.
        """
        return parsed

    def decide(self, obs: Observation) -> Decision:
        visible = self.visible_observation(obs)
        prompt = build_prompt(visible)
        response = self.client.complete(SYSTEM_PROMPT, prompt)

        if response is None:
            parsed = fallback("LLM call failed")
        else:
            parsed = parse_response(response.text)
        parsed = self.apply_guardrails(parsed, visible)

        target = action_to_target(parsed, obs.replicas_total)
        return Decision(
            target_replicas=target,
            info={
                "reason": f"{parsed.action} {parsed.amount}: {parsed.reason}",
                "invalid": parsed.invalid,
                "llm_called": True,
                "llm_latency_s": response.latency_s if response else None,
                "llm_cached": response.cached if response else False,
                "llm_raw": response.text if response else "",
            },
        )
