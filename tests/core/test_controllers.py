"""Implemented controllers (static, human) and the LLM controller's plumbing."""

import pytest

import controller.llm_controller as llm_mod
from controller.human import HumanController, QuitGame, parse_move
from controller.llm_client import LLMResponse
from controller.llm_controller import LLMController, ParsedAction, action_to_target
from controller.static import StaticController
from tests.helpers import make_obs

# ------------------------------------------------------------ static


def test_static_always_returns_its_count():
    c = StaticController(replicas=5)
    c.reset()
    for cpu in (5, 50, 100):
        assert c.decide(make_obs(cpu_pct=cpu)).target_replicas == 5
    assert c.name == "static5"


# ------------------------------------------------------------ human


@pytest.mark.parametrize(
    "raw, change",
    [("", 0), ("+3", 3), ("-2", -2), (" + 1 ", 1), ("3", None), ("abc", None)],
)
def test_parse_move(raw, change):
    assert parse_move(raw) == change


def test_parse_move_quit():
    with pytest.raises(QuitGame):
        parse_move("q")


def test_human_controller_reprompts_on_bad_input_and_shows_notes():
    inputs = iter(["banana", "+2"])
    printed = []
    c = HumanController(input_fn=lambda _: next(inputs), print_fn=printed.append)
    obs = make_obs(
        replicas_ready=3, replicas_pending=1, operator_notes=["big sale soon"]
    )
    decision = c.decide(obs)
    assert decision.target_replicas == 6  # 3 ready + 1 pending + 2
    output = "\n".join(printed)
    assert "OPERATOR NOTE: big sale soon" in output
    assert "??" in output  # complained about "banana"


# ------------------------------------------------------------ LLM plumbing (no stubs needed)


class FakeClient:
    """Stands in for LLMClient: records prompts, returns a canned reply (or None)."""

    model = "fake"

    def __init__(self, reply="{}"):
        self.reply = reply
        self.prompts = []

    def complete(self, system, prompt, use_cache=True):
        self.prompts.append(prompt)
        if self.reply is None:
            return None
        return LLMResponse(self.reply, latency_s=0.25, cached=False, model="fake")


@pytest.fixture
def stubbed(monkeypatch):
    """Replace the team's stubs with trivial versions so we can test the plumbing."""
    monkeypatch.setattr(
        llm_mod,
        "build_prompt",
        lambda obs: repr(obs.operator_notes) + repr(obs.history),
    )
    monkeypatch.setattr(
        llm_mod, "parse_response", lambda text: ParsedAction("SCALE_UP", 2, "test")
    )


def test_metric_only_variant_never_sees_notes():
    obs = make_obs(operator_notes=["campaign soon"], history=[{"tick": 0}])
    assert (
        LLMController(FakeClient(), use_context=False)
        .visible_observation(obs)
        .operator_notes
        == []
    )
    assert LLMController(FakeClient(), use_context=True).visible_observation(
        obs
    ).operator_notes == ["campaign soon"]
    assert (
        LLMController(FakeClient(), use_history=False).visible_observation(obs).history
        == []
    )


def test_variant_names():
    assert LLMController(FakeClient()).name == "llm"
    assert LLMController(FakeClient(), use_context=True).name == "llm_context"
    assert LLMController(FakeClient(), use_history=False).name == "llm_nohist"


def test_llm_decide_end_to_end(stubbed):
    client = FakeClient()
    c = LLMController(client, use_context=False)
    d = c.decide(make_obs(replicas_ready=3, operator_notes=["secret"]))
    assert d.target_replicas == 5
    assert d.info["llm_called"] and d.info["llm_latency_s"] == 0.25
    assert not d.info["invalid"]
    assert "secret" not in client.prompts[0]


def test_llm_call_failure_falls_back_to_no_change(stubbed):
    c = LLMController(FakeClient(reply=None))
    d = c.decide(make_obs(replicas_ready=4))
    assert d.target_replicas == 4
    assert d.info["invalid"] is True


@pytest.mark.parametrize(
    "action, amount, expected",
    [("SCALE_UP", 2, 7), ("SCALE_DOWN", 3, 2), ("NO_CHANGE", 0, 5)],
)
def test_action_to_target(action, amount, expected):
    assert action_to_target(ParsedAction(action, amount), current_total=5) == expected
