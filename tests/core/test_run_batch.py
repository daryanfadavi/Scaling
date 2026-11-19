"""Offline checks for experiments/run_batch.py: the run order and the LLM health check."""

import importlib.util
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("run_batch", REPO_ROOT / "experiments" / "run_batch.py")
run_batch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run_batch)


def test_plan_is_the_full_matrix_interleaved():
    runs = run_batch.plan()
    assert len(runs) == 24
    keys = {(r["scenario"], r["controller"], r["repeat"]) for r in runs}
    assert len(keys) == 24  # every combination exactly once
    assert [r["repeat"] for r in runs] == [1] * 12 + [2] * 12  # all of repeat 1 first
    for i in range(0, 24, 4):  # each scenario block runs every controller once
        block = runs[i:i + 4]
        assert len({r["scenario"] for r in block}) == 1
        assert sorted(r["controller"] for r in block) == sorted(run_batch.CONTROLLERS)
    firsts = [runs[i]["controller"] for i in range(0, 24, 4)]
    assert len(set(firsts)) > 1  # the controller that goes first rotates
    assert {r["scenario"]: r["duration_s"] for r in runs} == {
        "daily_wave": 600, "random_bursts": 600, "busy_day": 900}


def write_log(folder, ticks):
    folder.mkdir(parents=True)
    lines = [json.dumps({"event": "start", "model": "m"})] + [json.dumps(t) for t in ticks]
    (folder / "controller.log").write_text("\n".join(lines) + "\n")


def test_llm_run_with_mostly_failed_calls_is_bad(tmp_path):
    failed = {"tick": 0, "reason": "fallback: LLM call failed", "invalid": True}
    ok = {"tick": 1, "reason": "load is steady", "invalid": False, "llm_latency_s": 1.0}
    write_log(tmp_path / "a", [failed] * 6 + [ok] * 2)
    health = run_batch.llm_health(tmp_path / "a")
    assert health["bad"] and health["model_calls_failed"] == 6 and health["decisions"] == 8

    write_log(tmp_path / "b", [failed] * 2 + [ok] * 6)
    assert not run_batch.llm_health(tmp_path / "b")["bad"]


def test_llm_run_without_decisions_is_bad(tmp_path):
    write_log(tmp_path / "c", [])
    assert run_batch.llm_health(tmp_path / "c")["bad"]
    assert run_batch.llm_health(tmp_path / "missing")["bad"]
