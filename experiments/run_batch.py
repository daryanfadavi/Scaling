"""Run the showcase batch: every controller on every scenario, twice, unattended.

    python experiments/run_batch.py --dry-run    # print the plan and estimated finish time
    python experiments/run_batch.py              # run it (takes hours: see the estimate)
    python experiments/run_batch.py --resume     # skip runs whose results already exist

Each run is one call of experiments/run_experiment.py, so every run goes through
exactly the same procedure (reset, settle, load test, telemetry, cleanup).

Order: repeat 1 of everything, then repeat 2. Within each scenario the controllers
take turns, and the starting controller rotates, so slow drift on the laptop
(heat, background load) doesn't consistently favour one controller.

If a run fails, it's logged and the batch moves on (the runner moves its partial
output to repeat_N_failed_<time>/).

LLM health check: after each LLM run, controller.log is scored with the same code as
experiments/summarize_cluster.py. If most model calls failed (e.g. out of credit, bad
key), the run is marked BAD and moved to repeat_N_bad_<time>/, so it isn't scored as a
real result and --resume redoes it. The controller holds steady when a call fails, so
without this check such a run would look like a calm, cheap controller. After two BAD
LLM runs in a row, the remaining LLM runs are skipped; the baselines keep running.

Writes experiments/results/batch_<timestamp>.log (everything, including each run's
output) and batch_<timestamp>.json (one entry per run: times, status, LLM health).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = REPO_ROOT / "experiments" / "results"
sys.path.insert(0, str(REPO_ROOT / "experiments"))
from summarize_cluster import _read_log  # noqa: E402  (same failure counting as the final scoring)

# ---- The matrix ------------------------------------------------------------------
CONTROLLERS = ["hpa", "keda", "phpa", "llm"]
SCENARIOS = {"daily_wave": 600, "random_bursts": 600, "busy_day": 900}  # name: load test seconds
REPEATS = 2
# Time a run takes on top of its load test: reset, 60 s settle, 30 s final scrape,
# PHPA's helm install, ... Measured from the smoke tests; only used for the estimate.
OVERHEAD_S = 100  # smoke tests: 120 s load took 217 s end to end
# LLM health check
LLM_CONTROLLERS = {"llm"}
BAD_FAILED_SHARE = 0.5      # a run is BAD if more than this share of model calls failed
BAD_IN_A_ROW_TO_SKIP = 2    # this many BAD LLM runs in a row: skip the remaining LLM runs
# -----------------------------------------------------------------------------------


def plan() -> list[dict]:
    """The runs in order: repeat 1 of everything, then repeat 2, controllers rotating."""
    runs = []
    for repeat in range(1, REPEATS + 1):
        for i, (scenario, duration) in enumerate(SCENARIOS.items()):
            shift = (i + repeat - 1) % len(CONTROLLERS)
            for controller in CONTROLLERS[shift:] + CONTROLLERS[:shift]:
                runs.append({"scenario": scenario, "controller": controller,
                             "repeat": repeat, "duration_s": duration})
    return runs


def run_dir(run: dict) -> Path:
    return RESULTS_DIR / run["scenario"] / run["controller"] / f"repeat_{run['repeat']}"


def estimate_s(runs: list[dict]) -> int:
    return sum(r["duration_s"] + OVERHEAD_S for r in runs)


def llm_health(folder: Path) -> dict:
    """Model-call counts from controller.log, and whether the run is BAD."""
    log_file = folder / "controller.log"
    if not log_file.exists():
        return {"bad": True, "why": "no controller.log"}
    counts = _read_log(log_file)
    decisions, failed = counts["decisions"], counts["model_calls_failed"]
    health = {"model": counts["model"], "decisions": decisions, "model_calls_failed": failed,
              "invalid_answers": counts["invalid_answers"], "observe_errors": counts["observe_errors"]}
    if decisions == 0:
        health.update(bad=True, why="no decisions logged")
    elif failed / decisions > BAD_FAILED_SHARE:
        health.update(bad=True, why=f"{failed} of {decisions} model calls failed")
    else:
        health.update(bad=False, why="")
    return health


class Batch:
    def __init__(self, stamp: str):
        self.log_path = RESULTS_DIR / f"batch_{stamp}.log"
        self.json_path = RESULTS_DIR / f"batch_{stamp}.json"
        self.entries: list[dict] = []
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        self.log_file = open(self.log_path, "a", buffering=1)

    def say(self, message: str) -> None:
        line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] BATCH {message}"
        print(line, flush=True)
        self.log_file.write(line + "\n")

    def record(self, entry: dict) -> None:
        self.entries.append(entry)
        self.json_path.write_text(json.dumps(self.entries, indent=1))

    def run_one(self, run: dict) -> str:
        """Run one experiment; output goes into the batch log. Returns PASS or FAIL."""
        cmd = [sys.executable, str(REPO_ROOT / "experiments" / "run_experiment.py"),
               "--controller", run["controller"], "--scenario", run["scenario"],
               "--only-repeat", str(run["repeat"]), "--duration", str(run["duration_s"])]
        process = subprocess.Popen(cmd, cwd=REPO_ROOT, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True)
        for line in process.stdout:
            sys.stdout.write(line)
            self.log_file.write(line)
        process.wait()
        return "PASS" if process.returncode == 0 and run_dir(run).exists() else "FAIL"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="print the plan, run nothing")
    parser.add_argument("--resume", action="store_true", help="skip runs whose results already exist")
    args = parser.parse_args()

    runs = plan()
    existing = [r for r in runs if run_dir(r).exists()]
    todo = [r for r in runs if not run_dir(r).exists()] if args.resume else runs
    if existing and not args.resume:
        print(f"{len(existing)} of these runs already have results, e.g. {run_dir(existing[0])}.\n"
              "Use --resume to skip them, or move them away to start fresh.")
        if not args.dry_run:
            return 1

    total = estimate_s(todo)
    finish = datetime.now() + timedelta(seconds=total)
    print(f"{len(todo)} run(s) to do ({len(runs) - len(todo)} already done). "
          f"Estimated {total / 3600:.1f} h, finishing around {finish:%a %H:%M} if started now.")
    print(f"  {'#':>2}  {'repeat':<6} {'scenario':<14} {'controller':<10} {'load':>6}  est. start")
    t = datetime.now()
    for i, r in enumerate(todo, 1):
        print(f"  {i:>2}  {r['repeat']:<6} {r['scenario']:<14} {r['controller']:<10} "
              f"{r['duration_s']:>5}s  {t:%H:%M}")
        t += timedelta(seconds=r["duration_s"] + OVERHEAD_S)
    if args.dry_run:
        return 0

    batch = Batch(datetime.now().strftime("%Y%m%d_%H%M%S"))
    batch.say(f"start: {len(todo)} runs, estimated {total / 3600:.1f} h, finish ~{finish:%a %H:%M}")
    bad_in_a_row, skip_llm = 0, False
    for i, run in enumerate(todo, 1):
        name = f"{run['scenario']}/{run['controller']}/repeat_{run['repeat']}"
        entry = {**run, "index": i}
        is_llm = run["controller"] in LLM_CONTROLLERS
        if is_llm and skip_llm:
            batch.say(f"({i}/{len(todo)}) SKIP {name}: {BAD_IN_A_ROW_TO_SKIP} BAD LLM runs in a row")
            batch.record({**entry, "status": "SKIPPED"})
            continue
        if run_dir(run).exists():  # --resume, or finished while we were running
            batch.say(f"({i}/{len(todo)}) SKIP {name}: results already exist")
            batch.record({**entry, "status": "EXISTS"})
            continue

        started = datetime.now()
        batch.say(f"({i}/{len(todo)}) START {name} ({run['duration_s']} s load)")
        status = batch.run_one(run)
        entry.update(start=started.isoformat(timespec="seconds"),
                     end=datetime.now().isoformat(timespec="seconds"))

        if is_llm and status == "PASS":
            health = llm_health(run_dir(run))
            entry["llm_health"] = health
            if health["bad"]:
                status = "BAD"
                moved = run_dir(run).with_name(f"repeat_{run['repeat']}_bad_{int(time.time())}")
                run_dir(run).rename(moved)
                health["moved_to"] = str(moved.relative_to(REPO_ROOT))
                bad_in_a_row += 1
                if bad_in_a_row >= BAD_IN_A_ROW_TO_SKIP:
                    skip_llm = True
                    batch.say(f"{bad_in_a_row} BAD LLM runs in a row: skipping the remaining LLM runs "
                              "(check the API key/credit, then rerun with --resume)")
            else:
                bad_in_a_row = 0
        entry["status"] = status
        batch.record(entry)

        took = (datetime.fromisoformat(entry["end"]) - started).total_seconds()
        detail = ""
        if "llm_health" in entry:
            h = entry["llm_health"]
            detail = (f"; model calls failed {h.get('model_calls_failed')}/{h.get('decisions')}, "
                      f"invalid {h.get('invalid_answers')}" + (f" -> {h['why']}" if h["bad"] else ""))
        batch.say(f"({i}/{len(todo)}) {status} {name} in {took / 60:.1f} min{detail}")

    counts = {s: sum(e["status"] == s for e in batch.entries) for s in
              ("PASS", "FAIL", "BAD", "SKIPPED", "EXISTS")}
    batch.say("done: " + ", ".join(f"{n} {s}" for s, n in counts.items() if n))
    return 0 if counts["FAIL"] == counts["BAD"] == counts["SKIPPED"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
