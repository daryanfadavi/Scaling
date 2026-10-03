"""The real SYSTEM_PROMPT, build_prompt and parse_response (no network: a fake client).

tests/todo/test_llm_parser.py is the original spec; these go further and also
check the fairness rules the comparison depends on.
"""

import json

import pytest

from controller.llm_client import LLMResponse
from controller.llm_controller import (
    SYSTEM_PROMPT,
    VALID_ACTIONS,
    LLMController,
    build_prompt,
    parse_response,
)
from experiments.run import resolve_scenario
from simulator.env import ScalingEnv, run_episode
from simulator.metrics import summarize
from tests.helpers import make_obs


class RecordingClient:
    """Stands in for LLMClient: records every (system, prompt), returns a canned reply."""

    model = "fake"

    def __init__(self, reply='{"action": "NO_CHANGE", "amount": 0, "reason": "steady"}'):
        self.reply = reply
        self.systems = []
        self.prompts = []

    def complete(self, system, prompt, use_cache=True):
        self.systems.append(system)
        self.prompts.append(prompt)
        if self.reply is None:
            return None
        return LLMResponse(self.reply, latency_s=0.1, cached=False, model="fake")


def reply(action, amount, reason="because"):
    return json.dumps({"action": action, "amount": amount, "reason": reason})


def assert_fallback(parsed):
    assert (parsed.action, parsed.amount, parsed.invalid) == ("NO_CHANGE", 0, True)
    assert parsed.reason.startswith("fallback:")


# ------------------------------------------------------------ system prompt


def test_system_prompt_states_the_task_and_the_format():
    for action in VALID_ACTIONS:
        assert action in SYSTEM_PROMPT
    for key in ('"action"', '"amount"', '"reason"'):
        assert key in SYSTEM_PROMPT
    assert "300 ms" in SYSTEM_PROMPT  # the SLA
    assert "50 requests per second" in SYSTEM_PROMPT  # capacity per replica
    assert "30 seconds" in SYSTEM_PROMPT  # startup delay


def test_system_prompt_example_is_a_valid_answer():
    """The example we show the model must itself pass our parser."""
    example = next(
        line for line in SYSTEM_PROMPT.splitlines() if line.startswith('{"action"')
    )
    parsed = parse_response(example)
    assert parsed.invalid is False and parsed.action in VALID_ACTIONS


def test_system_prompt_is_provider_agnostic():
    lowered = SYSTEM_PROMPT.lower()
    for word in ("claude", "anthropic", "openrouter", "gpt", "llama"):
        assert word not in lowered


def test_system_prompt_knows_nothing_about_the_experiment():
    """AGENTS.md: controllers must not know they're being evaluated."""
    lowered = SYSTEM_PROMPT.lower()
    for word in ("spike", "ramp", "scenario", "campaign", "hpa", "baseline", "experiment"):
        assert word not in lowered


def test_system_prompt_is_identical_across_variants():
    obs = make_obs(operator_notes=["note"], history=[{"tick": 0}])
    seen = set()
    for use_context in (False, True):
        for use_history in (False, True):
            client = RecordingClient()
            LLMController(client, use_context, use_history).decide(obs)
            seen.add(client.systems[0])
    assert seen == {SYSTEM_PROMPT}


# ------------------------------------------------------------ build_prompt


def test_prompt_shows_every_current_metric():
    prompt = build_prompt(
        make_obs(
            tick=7,
            replicas_ready=4,
            replicas_pending=2,
            cpu_pct=83.0,
            request_rate=166.0,
            latency_ms=271.0,
            backlog=37.0,
            min_replicas=1,
            max_replicas=20,
        )
    )
    assert "t=105s" in prompt and "tick 7" in prompt
    assert "4 ready" in prompt and "2 starting" in prompt
    assert "total 6" in prompt
    assert "min 1" in prompt and "max 20" in prompt
    assert "83%" in prompt and "166 req/s" in prompt
    assert "271 ms" in prompt and "37 requests" in prompt


def test_prompt_has_no_history_or_notes_sections_when_empty():
    prompt = build_prompt(make_obs())
    assert "history" not in prompt.lower()
    assert "Operator notes" not in prompt


def test_prompt_lists_every_note():
    prompt = build_prompt(make_obs(operator_notes=["first note", "second note"]))
    assert "Operator notes" in prompt
    assert "first note" in prompt and "second note" in prompt


def test_prompt_history_uses_the_applied_target():
    """`applied_target` is what the environment really did; `target` is only the request."""
    history = [
        {
            "tick": 3,
            "replicas_ready": 2,
            "replicas_pending": 1,
            "cpu_pct": 71.0,
            "request_rate": 99.0,
            "latency_ms": 123.0,
            "backlog": 0.0,
            "target": 17,
            "applied_target": 6,
        }
    ]
    row = build_prompt(make_obs(history=history)).splitlines()[-3]
    cells = [cell.strip() for cell in row.split("|")]
    assert cells[0] == "3" and cells[-1] == "6"
    assert "17" not in cells


def test_prompt_is_deterministic():
    """The disk cache is keyed on the prompt text, so it must not vary run to run."""
    obs = make_obs(operator_notes=["note"], history=[{"tick": 0, "applied_target": 3}])
    assert build_prompt(obs) == build_prompt(obs)


# ------------------------------------------------------------ parse_response


@pytest.mark.parametrize("action, amount", [("SCALE_UP", 1), ("SCALE_UP", 3), ("SCALE_DOWN", 2)])
def test_parse_valid_actions(action, amount):
    p = parse_response(reply(action, amount, "why"))
    assert (p.action, p.amount, p.reason, p.invalid) == (action, amount, "why", False)


@pytest.mark.parametrize(
    "text",
    [
        'Sure! {"action": "SCALE_UP", "amount": 2, "reason": "x"} Hope that helps.',
        '```\n{"action": "SCALE_UP", "amount": 2, "reason": "x"}\n```',
        '  \n{"action":"SCALE_UP","amount":2,"reason":"x"}\n\n',
        'Load is {high}. {"action": "SCALE_UP", "amount": 2, "reason": "x"}',
    ],
)
def test_parse_finds_json_inside_surrounding_text(text):
    p = parse_response(text)
    assert (p.action, p.amount, p.invalid) == ("SCALE_UP", 2, False)


def test_parse_takes_the_last_answer_when_the_model_changes_its_mind():
    text = reply("SCALE_DOWN", 1) + "\nActually, on reflection:\n" + reply("SCALE_UP", 3)
    p = parse_response(text)
    assert (p.action, p.amount) == ("SCALE_UP", 3)


@pytest.mark.parametrize("amount", [5, "lots", None, 1.5])
def test_no_change_ignores_whatever_amount_was_sent(amount):
    p = parse_response(reply("NO_CHANGE", amount))
    assert (p.action, p.amount, p.invalid) == ("NO_CHANGE", 0, False)


def test_missing_or_odd_reason_is_tolerated():
    assert parse_response('{"action": "SCALE_UP", "amount": 1}').reason == ""
    p = parse_response('{"action": "SCALE_UP", "amount": 1, "reason": 42}')
    assert p.invalid is False and p.reason == "42"


@pytest.mark.parametrize(
    "text",
    [
        reply("scale_up", 2),  # wrong case
        reply("SCALE_UP ", 2),  # trailing space
        reply("SCALE_UP", 2.0),  # a float, even a whole one
        '{"action": "SCALE_UP"}',  # no amount
        '{"action": null, "amount": 2}',
        '{"action": ["SCALE_UP"], "amount": 2}',
        '{"decision": {"action": "SCALE_UP", "amount": 99}}',  # nested, out of range
        "SCALE_UP 2",  # right idea, not JSON
    ],
)
def test_parse_rejects_near_misses(text):
    assert_fallback(parse_response(text))


@pytest.mark.parametrize("text", [None, 42, b"{}", [], {}, "{", "}{", "{{{{", "\x00", "null"])
def test_parse_never_raises(text):
    assert_fallback(parse_response(text))


# ------------------------------------------------------------ decide(), end to end


def test_decide_applies_a_valid_reply():
    client = RecordingClient(reply("SCALE_UP", 2, "cpu is high"))
    d = LLMController(client).decide(make_obs(replicas_ready=3, replicas_pending=1))
    assert d.target_replicas == 6  # relative to ready + starting
    assert d.info["invalid"] is False
    assert "cpu is high" in d.info["reason"]
    assert d.info["llm_raw"] == client.reply


@pytest.mark.parametrize("bad_reply", ["I would add replicas.", reply("SCALE_UP", 9), None])
def test_decide_holds_steady_on_a_bad_reply(bad_reply):
    d = LLMController(RecordingClient(bad_reply)).decide(make_obs(replicas_ready=4))
    assert d.target_replicas == 4
    assert d.info["invalid"] is True


# ------------------------------------------------------------ fairness, on a real episode


def run_variant(scenario_name, **flags):
    client = RecordingClient()
    env = ScalingEnv(resolve_scenario(scenario_name), seed=0)
    records = run_episode(env, LLMController(client, **flags))
    return client.prompts, summarize(records, env.config.tick_seconds)


def test_real_episode_prompts_are_well_formed():
    prompts, metrics = run_variant("spike_with_warning", use_context=True)
    assert len(prompts) == 80 and metrics["invalid_decisions"] == 0
    for prompt in prompts:
        assert "?" not in prompt  # every history field build_prompt asks for exists
    assert "Recent history" not in prompts[0]  # nothing has happened yet
    assert "Recent history" in prompts[10]


def test_only_the_context_variant_sees_the_operator_note():
    metric_only, _ = run_variant("spike_with_warning", use_context=False)
    with_context, _ = run_variant("spike_with_warning", use_context=True)

    assert not any("campaign" in p.lower() for p in metric_only)
    noted = [tick for tick, p in enumerate(with_context) if "campaign" in p.lower()]
    assert noted == list(range(24, 30))  # shown from tick 24 until the spike at 30

    # Outside those ticks the two variants get exactly the same prompt.
    for tick in range(24):
        assert metric_only[tick] == with_context[tick]


def test_no_history_variant_never_sees_history():
    prompts, _ = run_variant("spike", use_history=False)
    assert not any("history" in p.lower() for p in prompts)


@pytest.mark.parametrize("scenario_name", ["ramp", "spike", "spike_with_warning"])
def test_prompts_never_reveal_the_scenario(scenario_name):
    prompts, _ = run_variant(scenario_name, use_context=True)
    for prompt in prompts:
        lowered = prompt.lower()
        assert "spike" not in lowered and "ramp" not in lowered
        assert "scenario" not in lowered
