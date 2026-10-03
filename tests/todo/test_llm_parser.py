"""Spec for parse_response() and build_prompt() in controller/llm_controller.py (Pair B).

Run with:  pytest -m todo tests/todo/test_llm_parser.py
"""

import pytest

from controller.llm_controller import SYSTEM_PROMPT, build_prompt, parse_response
from tests.helpers import make_obs

pytestmark = pytest.mark.todo


def assert_fallback(parsed):
    assert parsed.action == "NO_CHANGE"
    assert parsed.invalid is True


# ------------------------------------------------------------ valid output


def test_valid_scale_up():
    p = parse_response('{"action": "SCALE_UP", "amount": 2, "reason": "cpu is high"}')
    assert (p.action, p.amount, p.invalid) == ("SCALE_UP", 2, False)
    assert p.reason == "cpu is high"


def test_valid_scale_down():
    p = parse_response('{"action": "SCALE_DOWN", "amount": 1, "reason": "idle"}')
    assert (p.action, p.amount, p.invalid) == ("SCALE_DOWN", 1, False)


def test_no_change_has_zero_amount():
    p = parse_response('{"action": "NO_CHANGE", "amount": 2, "reason": "steady"}')
    assert (p.action, p.amount, p.invalid) == ("NO_CHANGE", 0, False)


def test_json_inside_code_fence():
    text = 'Here is my decision:\n```json\n{"action": "SCALE_UP", "amount": 3, "reason": "spike"}\n```'
    p = parse_response(text)
    assert (p.action, p.amount, p.invalid) == ("SCALE_UP", 3, False)


# ------------------------------------------------------------ invalid output -> NO_CHANGE + invalid


@pytest.mark.parametrize(
    "text",
    [
        "I think you should add some replicas.",  # not JSON
        "",  # empty
        '{"action": "SCALE_UP", "amount": 2',  # truncated JSON
        "[1, 2, 3]",  # JSON, but not an object
    ],
)
def test_malformed_text_falls_back(text):
    assert_fallback(parse_response(text))


def test_unknown_action_falls_back():
    assert_fallback(parse_response('{"action": "PANIC", "amount": 2, "reason": "?"}'))


def test_missing_action_falls_back():
    assert_fallback(parse_response('{"amount": 2, "reason": "?"}'))


@pytest.mark.parametrize("amount", [0, 4, 10, -1])
def test_out_of_range_amount_falls_back(amount):
    assert_fallback(
        parse_response(f'{{"action": "SCALE_UP", "amount": {amount}, "reason": "x"}}')
    )


@pytest.mark.parametrize("amount", ['"2"', "1.5", "true", "null"])
def test_non_integer_amount_falls_back(amount):
    assert_fallback(
        parse_response(f'{{"action": "SCALE_DOWN", "amount": {amount}, "reason": "x"}}')
    )


# ------------------------------------------------------------ prompt


def test_system_prompt_written():
    assert "TODO" not in SYSTEM_PROMPT
    assert "SCALE_UP" in SYSTEM_PROMPT  # tells the model the output format


def test_prompt_contains_telemetry():
    prompt = build_prompt(make_obs(replicas_ready=3, cpu_pct=91.0, latency_ms=412.0))
    assert "91" in prompt and "412" in prompt


def test_prompt_includes_notes_only_when_present():
    with_note = build_prompt(
        make_obs(operator_notes=["Marketing campaign launches soon"])
    )
    without = build_prompt(make_obs())
    assert "Marketing campaign launches soon" in with_note
    assert "Marketing campaign" not in without
