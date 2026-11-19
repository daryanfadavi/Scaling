"""experiments/prompt_lab.py: replaying recorded situations against prompt variants. No cluster or API needed."""

import json
import threading

import pytest

import controllers.llm.main as llm_main
from controller.llm_client import LLMResponse
from controllers.llm.prompt import get_variant
from experiments.prompt_lab import agreement, cases_from_records, label, main, report, run_lab

UP = '{"action": "SCALE_UP", "amount": 2, "reason": "busy"}'
HOLD = '{"action": "NO_CHANGE", "amount": 0, "reason": "fine"}'


class ScriptedClient:
    """Answers from a list, in call order, and remembers the prompts."""
    model = "fake"

    def __init__(self, replies):
        self.replies, self.prompts, self.systems = list(replies), [], []
        self.lock = threading.Lock()

    def complete(self, system, prompt, use_cache=True):
        with self.lock:
            self.prompts.append(prompt)
            self.systems.append(system)
            reply = self.replies.pop(0) if self.replies else HOLD
        return None if reply is None else LLMResponse(reply, 0.1, False, "fake")


# ---- the key property: the lab rebuilds the prompts the live controller really sent ----------

def test_lab_reproduces_the_prompts_the_live_controller_sent(monkeypatch):
    snaps = iter([dict(replicas_ready=1.0, replicas_starting=0.0, cpu_utilization_pct=40 + 30 * i,
                       request_rate_rps=5.0 * i, latency_p50_ms=7.0, latency_p95_ms=15.0 + i, error_rate=0.0)
                  for i in range(8)])
    state = {"replicas": 1}
    monkeypatch.setattr(llm_main, "latest_snapshot", lambda: next(snaps))
    monkeypatch.setattr(llm_main, "read_current_replicas", lambda: state["replicas"])
    monkeypatch.setattr(llm_main, "scale_to", lambda t: state.update(replicas=t))
    # up (applied), up again too soon (blocked by cooldown), hold, an unusable answer, then holds
    client = ScriptedClient([UP, UP, HOLD, "garbage", HOLD, UP, HOLD, HOLD])
    now = [0.0]
    c = llm_main.Controller(client, threading.Event(), clock=lambda: now[0])
    records = []
    for _ in range(8):
        records.append(c.step())
        now[0] += 15.0

    assert any("blocked" in p for p in client.prompts) and any("invalid answer" in p for p in client.prompts)
    cases = cases_from_records(records, "t")
    assert len(cases) == 8
    variant = get_variant("cluster-v1")
    for case, sent in zip(cases, client.prompts):
        rebuilt = variant.build(case["snapshot"], case["current_replicas"], case["min_replicas"],
                                case["max_replicas"], case["history"])
        assert rebuilt == sent, case["id"]


def test_ticks_without_metrics_are_skipped():
    records = [{"tick": 0, "action": "NO_CHANGE", "applied": False, "error": "observe failed: x"},
               {"event": "start"}]
    assert cases_from_records(records, "t") == []


# ---- running variants and reporting ----------------------------------------------------------

CASE = {"id": "t:t1", "tick": 1, "source": "t", "current_replicas": 3, "min_replicas": 1, "max_replicas": 10,
        "snapshot": dict(replicas_ready=3.0, replicas_starting=0.0, cpu_utilization_pct=88.0,
                         request_rate_rps=34.6, latency_p50_ms=7.0, latency_p95_ms=14.0, error_rate=0.0),
        "history": [], "expect": ["NO_CHANGE", "SCALE_DOWN"]}


def test_run_lab_asks_every_variant_every_repeat_with_its_own_prompt():
    variants = [get_variant("cluster-v1"), get_variant("cluster-v2-trend")]
    client = ScriptedClient([])
    results = run_lab([CASE], variants, repeats=3, client=client, workers=1)
    assert len(results) == 6 and {r["variant"] for r in results} == {"cluster-v1", "cluster-v2-trend"}
    assert sum(1 for s in client.systems if "trend:" in s) == 3          # only the trend variant's system prompt


def test_report_counts_answers_and_agreement():
    variants = [get_variant("cluster-v1"), get_variant("cluster-v2-trend")]
    # v1 answers UP, UP, HOLD ; trend answers HOLD x3 (workers=1 keeps the call order)
    client = ScriptedClient([UP, UP, HOLD, HOLD, HOLD, HOLD])
    results = run_lab([CASE], variants, repeats=3, client=client, workers=1)
    assert [r["variant"] for r in results[:3]] == ["cluster-v1"] * 3
    assert agreement(results, [CASE]) == {"cluster-v1": (1, 3), "cluster-v2-trend": (3, 3)}
    text = report(results, [CASE], variants)
    assert "SCALE_UP+2 x2" in text and "sensible 3/3" in text and "100%" in text and "33%" in text


def test_failed_and_invalid_answers_are_counted_as_not_sensible():
    client = ScriptedClient([None, "garbage", HOLD])
    results = run_lab([CASE], [get_variant("cluster-v1")], repeats=3, client=client, workers=1)
    assert [label(r) for r in results] == ["CALL FAILED", "INVALID", "NO_CHANGE"]
    assert agreement(results, [CASE]) == {"cluster-v1": (1, 3)}
    assert "1 failed, 1 invalid" in report(results, [CASE], [get_variant("cluster-v1")])


# ---- command line ----------------------------------------------------------------------------

def test_extract_and_dry_run_need_no_api(tmp_path, capsys):
    log = tmp_path / "controller.log"
    log.write_text("\n".join(json.dumps(r) for r in [
        {"event": "start"},
        {"tick": 0, "snapshot": CASE["snapshot"], "replicas_before": 3, "replicas_target": 3,
         "action": "NO_CHANGE", "amount": 0, "applied": False, "invalid": False, "reason": "ok"},
    ]) + "\n")
    out = tmp_path / "cases.json"
    assert main(["extract", str(log), "--name", "x", "--out", str(out)]) == 0
    assert json.loads(out.read_text())[0]["id"] == "x:t0"
    assert main(["run", "--cases", str(out), "--variants", "cluster-v2-trend", "--repeats", "2", "--dry-run"]) == 0
    printed = capsys.readouterr().out
    assert "1 cases x 1 variants x 2 repeats = 2 model calls" in printed and "Decide this tick's action" in printed


def test_big_runs_need_confirmation(tmp_path):
    out = tmp_path / "cases.json"
    out.write_text(json.dumps([CASE]))
    with pytest.raises(SystemExit, match="--yes"):
        main(["run", "--cases", str(out), "--repeats", "100"])
