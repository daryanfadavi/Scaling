"""Prompt variants (controllers/llm/prompt.py): each differs from the baseline by one thing only.

No cluster, Prometheus or API key needed.
"""

import threading

import pytest

import controllers.llm.main as llm_main
from controller.llm_client import LLMResponse
from controllers.llm.prompt import (
    DEFAULT_VARIANT,
    SYSTEM_PROMPT,
    TREND_NOTE,
    VARIANTS,
    WORDS_NOTE,
    OUTCOMES_NOTE,
    _build_v2,
    build_prompt,
    did_text,
    get_variant,
    history_entry,
    trend_line,
)

SNAP = dict(replicas_ready=3.0, replicas_starting=0.0, cpu_utilization_pct=88.0, request_rate_rps=34.6,
            latency_p50_ms=7.0, latency_p95_ms=14.3, error_rate=0.0)


def past(rps, cpu, p95=14.0, did="NO_CHANGE"):
    return dict(replicas_ready=3.0, replicas_starting=0.0, cpu_utilization_pct=cpu, request_rate_rps=rps,
                latency_p95_ms=p95, error_rate=0.0, replicas_requested=3, action=did)


# a falling load, oldest first
FALLING = [past(53.6, 133), past(51.4, 119), past(48.8, 110)]


# ------------------------------------------------------------ the baseline is untouched

def test_baseline_variant_is_exactly_the_original_prompt():
    v1 = get_variant("cluster-v1")
    assert v1.system_prompt == SYSTEM_PROMPT
    for history in ([], FALLING):
        assert v1.build(SNAP, 3, 1, 10, history) == build_prompt(SNAP, 3, 1, 10, history)


def test_v2_builder_with_everything_off_matches_the_original():
    for history in ([], FALLING):
        assert _build_v2(SNAP, 3, 1, 10, history) == build_prompt(SNAP, 3, 1, 10, history)


def test_default_variant_is_the_baseline():
    assert DEFAULT_VARIANT == "cluster-v1"


def test_unknown_variant_lists_the_choices():
    with pytest.raises(ValueError, match="cluster-v1"):
        get_variant("nope")


# ------------------------------------------------------------ each variant changes one thing

def test_trend_variant_adds_one_line_and_one_system_sentence():
    v = get_variant("cluster-v2-trend")
    base = build_prompt(SNAP, 3, 1, 10, FALLING).split("\n")
    new = v.build(SNAP, 3, 1, 10, FALLING).split("\n")
    added = [line for line in new if line not in base]
    assert len(added) == 1 and added[0].startswith("Trend over the last 4 readings")
    assert v.system_prompt.replace("\n" + TREND_NOTE, "") == SYSTEM_PROMPT


def test_words_variant_only_adds_words_in_brackets():
    v = get_variant("cluster-v2-words")
    text = v.build(SNAP, 3, 1, 10, FALLING)
    assert "CPU: 88% (moderate)" in text and "p95 14 ms (low)" in text and "Errors: 0.0% (none)" in text
    assert "Request rate: 34.6 req/s" in text          # numbers are all still there
    assert v.system_prompt.replace("\n" + WORDS_NOTE, "") == SYSTEM_PROMPT


def test_nohist_variant_hides_past_decisions():
    v = get_variant("cluster-v1-nohist")
    assert "Your recent decisions" not in v.build(SNAP, 3, 1, 10, FALLING)
    assert v.system_prompt == SYSTEM_PROMPT


def test_variants_never_mention_the_experiment():
    for v in VARIANTS.values():
        text = (v.system_prompt + v.build(SNAP, 3, 1, 10, FALLING)).lower()
        for word in ("scenario", "baseline", "hpa", "keda", "experiment", "evaluat", "benchmark"):
            assert word not in text, (v.name, word)


# ------------------------------------------------------------ the trend line

def test_trend_reports_a_falling_load():
    line = trend_line(SNAP, FALLING)
    assert "request rate 53.6 req/s -> 34.6 req/s (falling)" in line
    assert "CPU 133% -> 88% (falling)" in line
    assert "p95 latency 14 ms -> 14 ms (flat)" in line


def test_trend_reports_a_rising_load_and_small_changes_as_flat():
    rising = [past(5.0, 20), past(9.0, 40), past(14.0, 70)]
    line = trend_line(dict(SNAP, request_rate_rps=26.0, cpu_utilization_pct=130.0), rising)
    assert "(rising)" in line.split("request rate")[1].split(",")[0]
    assert "(flat)" in trend_line(dict(SNAP, request_rate_rps=50.5), [past(50.0, 88), past(50.2, 88)]).split("CPU")[0]


def test_trend_needs_history_and_handles_missing_data():
    assert trend_line(SNAP, []) is None
    assert "n/a" in trend_line(dict(SNAP, latency_p95_ms=None), FALLING)


# ------------------------------------------------------------ history helpers (shared with the loop)

def test_did_text_matches_what_the_model_is_told():
    assert did_text("SCALE_UP", 2, False, True) == "SCALE_UP 2"
    assert did_text("NO_CHANGE", 0, False, False) == "NO_CHANGE"
    assert did_text("SCALE_UP", 2, False, False) == "SCALE_UP 2 (blocked by guardrails, nothing changed)"
    assert did_text("NO_CHANGE", 0, True, False) == "invalid answer, nothing changed"


def test_history_entry_has_the_fields_the_table_prints():
    e = history_entry(SNAP, 3, "NO_CHANGE")
    assert e["replicas_requested"] == 3 and e["action"] == "NO_CHANGE" and e["cpu_utilization_pct"] == 88.0


# ------------------------------------------------------------ the loop uses the chosen variant

class FakeClient:
    model = "fake-model"

    def __init__(self):
        self.systems, self.prompts = [], []

    def complete(self, system, prompt, use_cache=True):
        self.systems.append(system)
        self.prompts.append(prompt)
        return LLMResponse('{"action": "NO_CHANGE", "amount": 0, "reason": "ok"}', 0.5, False, "fake-model")


@pytest.fixture
def cluster(monkeypatch):
    state = {"replicas": 3}
    monkeypatch.setattr(llm_main, "latest_snapshot", lambda: dict(SNAP))
    monkeypatch.setattr(llm_main, "read_current_replicas", lambda: state["replicas"])
    monkeypatch.setattr(llm_main, "scale_to", lambda t: None)
    return state


def run_ticks(variant_name, n=3):
    client = FakeClient()
    c = llm_main.Controller(client, threading.Event(), variant=get_variant(variant_name))
    records = [c.step() for _ in range(n)]
    return c, client, records


def test_loop_logs_and_uses_the_variant(cluster):
    c, client, records = run_ticks("cluster-v2-trend")
    assert all(r["prompt_version"] == "cluster-v2-trend" for r in records)
    assert client.systems[0] == get_variant("cluster-v2-trend").system_prompt
    assert "Trend over the last" not in client.prompts[0]      # first tick: only one reading
    assert "Trend over the last 2 readings" in client.prompts[1]


def test_nohist_loop_never_stores_or_shows_history(cluster):
    c, client, _ = run_ticks("cluster-v1-nohist", n=4)
    assert len(c.history) == 0
    assert all("Your recent decisions" not in p for p in client.prompts)


def test_default_loop_keeps_five_ticks_of_history(cluster):
    c, client, _ = run_ticks("cluster-v1", n=7)
    assert len(c.history) == llm_main.HISTORY_TICKS == 5
    assert "Your recent decisions" in client.prompts[-1]


def test_controller_without_a_variant_is_the_baseline(cluster):
    c = llm_main.Controller(FakeClient(), threading.Event())
    assert c.variant.name == "cluster-v1"


# ------------------------------------------------------------ diary variants (history length, outcomes)

LONG = [past(50.0 - i, 100 + i, did="NO_CHANGE") for i in range(12)]


def rows_in_table(text):
    return sum(1 for line in text.split("\n") if line[:1].isdigit() and " | " in line)


def test_default_history_length_is_one_number_everywhere():
    from controllers.llm.prompt import DEFAULT_HISTORY_TICKS
    assert DEFAULT_HISTORY_TICKS == llm_main.HISTORY_TICKS == 5
    assert get_variant("cluster-v1").history_ticks == DEFAULT_HISTORY_TICKS


def test_history_length_variants_show_that_many_decisions():
    for name, n in (("cluster-v1", 5), ("cluster-v1-hist10", 10), ("cluster-v1-hist15", 12), ("cluster-v1-nohist", 0)):
        text = get_variant(name).build(SNAP, 3, 1, 10, LONG)
        assert rows_in_table(text) == n, name
    assert get_variant("cluster-v1-hist15").system_prompt == SYSTEM_PROMPT


def test_outcomes_variant_adds_one_column_with_what_happened_next():
    v = get_variant("cluster-v2-outcomes")
    hist = [past(10.0, 215, p95=20.0, did="SCALE_UP 2"), past(35.0, 100, p95=23.0)]
    hist[0]["replicas_ready"] = 1.0
    hist[1]["replicas_ready"] = 3.0
    text = v.build(dict(SNAP, cpu_utilization_pct=137.0, latency_p95_ms=25.0), 3, 1, 10, hist)
    assert "you did | then (by the next reading)" in text
    assert "SCALE_UP 2 | pods ready 1->3, CPU 215->100%, p95 20->23 ms" in text
    assert "NO_CHANGE | pods ready 3->3" not in text and "CPU 100->137%, p95 23->25 ms" in text   # the last row compares with now
    assert v.system_prompt.replace("\n" + OUTCOMES_NOTE, "") == SYSTEM_PROMPT


def test_outcomes_variant_without_history_is_the_baseline_message():
    snap_prompt = build_prompt(SNAP, 3, 1, 10, [])
    assert get_variant("cluster-v2-outcomes").build(SNAP, 3, 1, 10, []) == snap_prompt


def test_outcome_text_handles_missing_data():
    from controllers.llm.prompt import _outcome_text
    assert _outcome_text({}, {}) == "n/a"
    assert _outcome_text({"cpu_utilization_pct": 50.0}, {"cpu_utilization_pct": None}) == "n/a"
