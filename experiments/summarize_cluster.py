"""Score and compare cluster experiment runs (the counterpart of plot.py's leaderboard, for the real cluster).

    python experiments/summarize_cluster.py                              # every run in experiments/results/
    python experiments/summarize_cluster.py --scenario sudden_spike
    python experiments/summarize_cluster.py --controller llm llm_trend   # only these controllers

Reads, for each run folder experiments/results/<scenario>/<controller>/repeat_<N>/:
    metrics.csv       every controller (docs/telemetry.md)
    controller.log    process controllers such as llm: one JSON line per decision
    metadata.json     for the git commit and whether the working tree was dirty

Writes next to the results (gitignored):
    cluster_runs.csv      one row per run
    cluster_summary.md    one row per scenario x controller: mean +/- std over repeats

What is scored (one number per run):
    sla_violation_ticks  15 s readings whose p95 latency is above 300 ms (the same limit as the simulator)
    pod_minutes          pods asked for x time, so pods still starting count too (the cost)
    peak_pods, mean_pods
    scale_events         readings where the requested pod count changed
    flaps                scale events that reverse the direction of the previous one (up then down, ...)
    p95_max_ms, error_rate_max
  and, for controllers with a controller.log:
    model_calls_failed   a failed API call is treated as "do nothing" by the controller, so it looks like calm.
                         Any run with failures is flagged: do NOT read it as the model choosing to hold.
    invalid_answers      replies that could not be parsed
    observe_errors       ticks where the metrics or kubectl could not be read
    guardrail_blocks     answers stopped by a cooldown

Runs whose folder name is repeat_<N>_failed_<time> are ignored.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = REPO_ROOT / "experiments" / "results"
SLA_MS = 300.0
RUN_DIR = re.compile(r"^repeat_(\d+)$")

# (column, label, decimals) for the summary table
SUMMARY_COLUMNS = (
    ("sla_violation_ticks", "SLA-violation ticks", 1),
    ("pod_minutes", "Pod-minutes", 1),
    ("p95_max_ms", "Peak p95 (ms)", 0),
    ("peak_pods", "Peak pods", 1),
    ("scale_events", "Scale events", 1),
    ("flaps", "Flaps", 1),
)


# ---- one run ------------------------------------------------------------------------------

def _scale_events(desired: list[float]) -> tuple[int, int]:
    """(number of changes, number that reverse the previous change's direction)."""
    changes = [b - a for a, b in zip(desired, desired[1:]) if b != a]
    flaps = sum(1 for a, b in zip(changes, changes[1:]) if (a > 0) != (b > 0))
    return len(changes), flaps


def _read_log(path: Path) -> dict:
    """Counts from a controller.log. Lines that are not JSON (tracebacks, warnings) are skipped."""
    start: dict = {}
    ticks: list[dict] = []
    for line in path.read_text(errors="replace").splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if record.get("event") == "start":
            start = record
        elif "tick" in record:
            ticks.append(record)
    latencies = [t["llm_latency_s"] for t in ticks if t.get("llm_latency_s") is not None]
    failed = sum(1 for t in ticks if str(t.get("reason", "")).startswith("fallback: LLM call failed"))
    return {
        "model": start.get("model"), "prompt_version": start.get("prompt_version"),
        "temperature": start.get("temperature"), "history_ticks": start.get("history_ticks"),
        "decisions": len(ticks),
        "applied": sum(1 for t in ticks if t.get("applied")),
        "model_calls_failed": failed,
        "invalid_answers": sum(1 for t in ticks if t.get("invalid")) - failed,
        "observe_errors": sum(1 for t in ticks if str(t.get("error", "")).startswith("observe failed")),
        "guardrail_blocks": sum(1 for t in ticks if "cooldown" in str(t.get("guardrail", ""))),
        "llm_latency_mean_s": round(statistics.mean(latencies), 2) if latencies else None,
    }


def score_run(run_dir: Path) -> dict:
    """Metrics for one run folder. Raises FileNotFoundError if metrics.csv is missing."""
    metrics = pd.read_csv(run_dir / "metrics.csv")
    interval = metrics["interval_s"].iloc[0] if "interval_s" in metrics and len(metrics) else 15
    desired = metrics["replicas_desired"].dropna().tolist()
    events, flaps = _scale_events(desired)
    p95 = metrics["latency_p95_ms"].dropna()
    errors = metrics["error_rate"].dropna()
    row = {
        "scenario": run_dir.parent.parent.name, "controller": run_dir.parent.name,
        "repeat": int(RUN_DIR.match(run_dir.name).group(1)), "ticks": len(metrics),
        "sla_violation_ticks": int((p95 > SLA_MS).sum()),
        "pod_minutes": round(float(sum(d * interval for d in desired)) / 60, 2),
        "peak_pods": max(desired) if desired else None,
        "mean_pods": round(statistics.mean(desired), 2) if desired else None,
        "scale_events": events, "flaps": flaps,
        "p95_max_ms": round(float(p95.max()), 1) if len(p95) else None,
        "error_rate_max": round(float(errors.max()), 4) if len(errors) else None,
    }
    log = run_dir / "controller.log"
    if log.exists():
        row.update(_read_log(log))
    meta = run_dir / "metadata.json"
    if meta.exists():
        git = json.loads(meta.read_text()).get("git", {})
        row["git_commit"] = str(git.get("commit", ""))[:8]
        row["git_dirty"] = git.get("dirty")
    return row


def suspect_reason(row: dict) -> str:
    """Why a run's numbers can't be trusted as the controller's own behavior ('' if nothing is wrong)."""
    problems = []
    if row.get("model_calls_failed"):
        problems.append(f"{row['model_calls_failed']} failed model calls (held by default, not by choice)")
    if row.get("observe_errors"):
        problems.append(f"{row['observe_errors']} ticks without metrics")
    if "decisions" in row and row["decisions"] == 0:
        problems.append("no decisions logged")
    return "; ".join(problems)


# ---- many runs ----------------------------------------------------------------------------

def collect(results_dir: Path, scenarios=None, controllers=None) -> list[dict]:
    rows = []
    for metrics in sorted(results_dir.glob("*/*/repeat_*/metrics.csv")):
        run_dir = metrics.parent
        if not RUN_DIR.match(run_dir.name):
            continue
        if scenarios and run_dir.parent.parent.name not in scenarios:
            continue
        if controllers and run_dir.parent.name not in controllers:
            continue
        row = score_run(run_dir)
        row["suspect"] = suspect_reason(row)
        rows.append(row)
    return rows


def _mean_std(values: list[float]) -> tuple[float | None, float | None]:
    values = [v for v in values if v is not None]
    if not values:
        return None, None
    return statistics.mean(values), (statistics.stdev(values) if len(values) > 1 else None)


def summarize(rows: list[dict]) -> list[dict]:
    """One row per (scenario, controller), with mean and std over its repeats."""
    groups: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        groups.setdefault((row["scenario"], row["controller"]), []).append(row)
    out = []
    for (scenario, controller), runs in sorted(groups.items()):
        item = {"scenario": scenario, "controller": controller, "runs": len(runs),
                "suspect_runs": sum(1 for r in runs if r["suspect"]),
                "prompt_version": next((r.get("prompt_version") for r in runs if r.get("prompt_version")), None)}
        for column, _, _ in SUMMARY_COLUMNS:
            item[column] = _mean_std([r.get(column) for r in runs])
        for column in ("model_calls_failed", "invalid_answers", "guardrail_blocks"):
            if any(column in r for r in runs):
                item[column] = sum(r.get(column, 0) for r in runs)
        out.append(item)
    return out


def _cell(pair, decimals: int) -> str:
    mean, std = pair
    if mean is None:
        return "n/a"
    return f"{mean:.{decimals}f}" if std is None else f"{mean:.{decimals}f} ± {std:.{decimals}f}"


def to_markdown(summary: list[dict]) -> str:
    if not summary:
        return "No runs found.\n"
    head = ["Scenario", "Controller", "Prompt", "Runs"] + [label for _, label, _ in SUMMARY_COLUMNS] + ["LLM failed / invalid", "Warning"]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for s in summary:
        llm = (f"{s['model_calls_failed']} / {s['invalid_answers']}" if "model_calls_failed" in s else "-")
        warn = f"⚠ {s['suspect_runs']} of {s['runs']} runs" if s["suspect_runs"] else ""
        cells = [s["scenario"], s["controller"], s["prompt_version"] or "-", str(s["runs"])]
        cells += [_cell(s[c], d) for c, _, d in SUMMARY_COLUMNS] + [llm, warn]
        lines.append("| " + " | ".join(cells) + " |")
    lines += ["", "Mean ± standard deviation over repeats (no ± means a single run, which is an anecdote, not a result).",
              "A ⚠ means some runs had failed model calls or missing metrics: those numbers are not the controller's own behavior."]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--results-dir", type=Path, default=RESULTS_DIR)
    parser.add_argument("--scenario", nargs="+", help="only these scenarios")
    parser.add_argument("--controller", nargs="+", help="only these controllers (folder names under controllers/)")
    args = parser.parse_args(argv)

    rows = collect(args.results_dir, args.scenario, args.controller)
    markdown = to_markdown(summarize(rows))
    if rows:
        pd.DataFrame(rows).to_csv(args.results_dir / "cluster_runs.csv", index=False)
        (args.results_dir / "cluster_summary.md").write_text(markdown)
    print(markdown)
    for row in rows:
        if row["suspect"]:
            print(f"WARNING {row['scenario']}/{row['controller']}/repeat_{row['repeat']}: {row['suspect']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
