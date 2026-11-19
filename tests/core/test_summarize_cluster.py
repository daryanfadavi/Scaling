"""experiments/summarize_cluster.py: scoring and comparing cluster runs. No cluster needed."""

import json

import pytest

from experiments.summarize_cluster import collect, main, score_run, suspect_reason, summarize, to_markdown

HEADER = ("timestamp,unix_time,elapsed_s,interval_s,replicas_desired,replicas_ready,replicas_starting,"
          "cpu_utilization_pct,request_rate_rps,latency_p50_ms,latency_p95_ms,error_rate\n")


def make_run(root, scenario, controller, repeat, desired, p95, errors=None, log=None, dirty=True):
    d = root / scenario / controller / f"repeat_{repeat}"
    d.mkdir(parents=True)
    rows = []
    for i, (des, lat) in enumerate(zip(desired, p95)):
        err = errors[i] if errors else 0.0
        rows.append(f"t,{i},{i * 15},15,{des},{des},0,50,10,5,{'' if lat is None else lat},{err}")
    (d / "metrics.csv").write_text(HEADER + "\n".join(rows) + "\n")
    (d / "metadata.json").write_text(json.dumps({"git": {"commit": "abcdef0123456789", "dirty": dirty}}))
    if log is not None:
        (d / "controller.log").write_text("\n".join(json.dumps(r) if isinstance(r, dict) else r for r in log) + "\n")
    return d


def tick(n, action="NO_CHANGE", applied=False, invalid=False, reason="ok", guardrail="", error=None, latency=1.0):
    r = {"tick": n, "action": action, "applied": applied, "invalid": invalid, "reason": reason,
         "guardrail": guardrail, "llm_latency_s": latency}
    if error:
        r["error"] = error
    return r


START = {"event": "start", "model": "m", "prompt_version": "cluster-v1", "temperature": None, "history_ticks": 5}


def test_scores_cost_latency_and_scaling(tmp_path):
    d = make_run(tmp_path, "s", "hpa", 1, desired=[1, 1, 3, 3, 2, 4], p95=[None, 100, 400, 250, 301, 20])
    row = score_run(d)
    assert row["sla_violation_ticks"] == 2                 # 400 and 301; the missing value is not a violation
    assert row["pod_minutes"] == pytest.approx((1 + 1 + 3 + 3 + 2 + 4) * 15 / 60)
    assert row["peak_pods"] == 4 and row["scale_events"] == 3
    assert row["flaps"] == 2                                # up, down, up: two reversals
    assert row["p95_max_ms"] == 400
    assert row["git_commit"] == "abcdef01" and row["git_dirty"] is True
    assert "decisions" not in row                           # HPA has no controller.log


def test_flaps_are_zero_for_a_steady_climb(tmp_path):
    row = score_run(make_run(tmp_path, "s", "hpa", 1, desired=[1, 2, 3, 3, 5], p95=[10] * 5))
    assert row["scale_events"] == 3 and row["flaps"] == 0


def test_reads_the_llm_log_and_ignores_stray_lines(tmp_path):
    log = [START, tick(0), "Traceback (most recent call last):", tick(1, "SCALE_UP", applied=True),
           tick(2, "SCALE_DOWN", guardrail="scale-down cooldown: scaled 30s ago (hold 120s)"),
           tick(3, invalid=True, reason="not json")]
    row = score_run(make_run(tmp_path, "s", "llm", 1, [1, 2, 2], [10, 10, 10], log=log))
    assert row["decisions"] == 4 and row["applied"] == 1
    assert row["invalid_answers"] == 1 and row["model_calls_failed"] == 0
    assert row["guardrail_blocks"] == 1
    assert row["prompt_version"] == "cluster-v1" and row["history_ticks"] == 5
    assert suspect_reason(row) == ""


def test_failed_model_calls_are_flagged_not_mistaken_for_calm(tmp_path):
    bad = [START] + [tick(i, invalid=True, reason="fallback: LLM call failed", latency=None) for i in range(3)]
    row = score_run(make_run(tmp_path, "s", "llm", 1, [1, 1, 1], [10, 10, 10], log=bad))
    assert row["model_calls_failed"] == 3 and row["invalid_answers"] == 0
    assert "3 failed model calls" in suspect_reason(row)


def test_missing_metrics_ticks_are_flagged(tmp_path):
    log = [START, tick(0, error="observe failed: prometheus down", latency=None)]
    row = score_run(make_run(tmp_path, "s", "llm", 1, [1], [10], log=log))
    assert row["observe_errors"] == 1 and "without metrics" in suspect_reason(row)


def test_summary_averages_repeats_and_skips_failed_run_folders(tmp_path):
    make_run(tmp_path, "s", "llm", 1, [1, 2], [100, 400], log=[START, tick(0)])
    make_run(tmp_path, "s", "llm", 2, [1, 2], [100, 100], log=[START, tick(0)])
    make_run(tmp_path, "s", "hpa", 1, [1, 1], [10, 10])
    (tmp_path / "s" / "llm" / "repeat_3_failed_123").mkdir()
    (tmp_path / "s" / "llm" / "repeat_3_failed_123" / "metrics.csv").write_text(HEADER)
    rows = collect(tmp_path)
    assert len(rows) == 3
    summary = {(s["controller"]): s for s in summarize(rows)}
    assert summary["llm"]["runs"] == 2
    assert summary["llm"]["sla_violation_ticks"][0] == pytest.approx(0.5)   # (1 + 0) / 2
    assert summary["hpa"]["sla_violation_ticks"][1] is None                 # one run: no std


def test_filters_and_markdown_output(tmp_path):
    make_run(tmp_path, "a", "llm", 1, [1], [10], log=[START, tick(0, invalid=True, reason="fallback: LLM call failed")])
    make_run(tmp_path, "b", "hpa", 1, [1], [10])
    assert {r["scenario"] for r in collect(tmp_path, scenarios=["a"])} == {"a"}
    assert {r["controller"] for r in collect(tmp_path, controllers=["hpa"])} == {"hpa"}
    md = to_markdown(summarize(collect(tmp_path)))
    assert "⚠ 1 of 1 runs" in md and "single run" in md
    assert to_markdown([]) == "No runs found.\n"


def test_main_writes_the_files(tmp_path, capsys):
    make_run(tmp_path, "s", "hpa", 1, [1, 2], [10, 10])
    assert main(["--results-dir", str(tmp_path)]) == 0
    assert (tmp_path / "cluster_runs.csv").exists() and (tmp_path / "cluster_summary.md").exists()
    assert "| s | hpa |" in capsys.readouterr().out
