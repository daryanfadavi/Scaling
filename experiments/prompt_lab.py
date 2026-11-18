"""Prompt lab: replay recorded cluster situations against prompt variants, without a cluster.

Every tick of a cluster run is already in controller.log (the metrics the model saw, the pod count, its
recent history). This tool turns those ticks into "cases" and asks each prompt variant what it would do,
several times, so you can see how a prompt change affects decisions on the same situations.

    # 1. save the situations of a finished run as cases (small JSON, safe to commit)
    python experiments/prompt_lab.py extract experiments/results/gradual_ramp/llm/repeat_1/controller.log \\
        --name gradual_ramp_run1 --out experiments/prompt_lab_cases/gradual_ramp_run1.json

    # 2. ask the variants (makes live API calls: cases x variants x repeats)
    python experiments/prompt_lab.py run --cases experiments/prompt_lab_cases/gradual_ramp_run1.json \\
        --ticks 9 15 --variants cluster-v1 cluster-v2-trend --repeats 5
    python experiments/prompt_lab.py run --cases ... --dry-run          # show the call count and one prompt

A case may carry "expect": the actions that count as sensible (a human judgement, written in "expect_note").
The lab then reports how often each variant agreed.

What this is and is not: it is OPEN-LOOP. Each case is a frozen moment, so it tests the quality of one decision
and not what the decision would cause later. Use it to shortlist prompts cheaply; confirm the shortlist on the
cluster with repeats (experiments/run_experiment.py, then experiments/summarize_cluster.py).
Output model: temperature is not sent (the API rejects it), so repeats differ; that is why there are repeats.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from controller.llm_controller import parse_response  # noqa: E402
from controllers.llm.prompt import VARIANTS, did_text, get_variant, history_entry  # noqa: E402

HISTORY_KEPT = 15         # recorded per case; each variant then uses as many as it shows (5 for the baseline)
RESULTS_DIR = REPO_ROOT / "experiments" / "results" / "prompt_lab"


# ---- extract: controller.log -> cases -----------------------------------------------------

def cases_from_records(records: list[dict], name: str, min_replicas: int = 1, max_replicas: int = 10) -> list[dict]:
    """One case per tick that had metrics. The recorded history is rebuilt the way the controller built it."""
    cases, history = [], []
    for record in records:
        if "tick" not in record or "snapshot" not in record:
            continue
        current = record["replicas_before"]
        cases.append({
            "id": f"{name}:t{record['tick']}", "tick": record["tick"], "source": name,
            "snapshot": record["snapshot"], "current_replicas": current,
            "min_replicas": min_replicas, "max_replicas": max_replicas,
            "history": list(history[-HISTORY_KEPT:]),
            "recorded_answer": {"action": record.get("action"), "amount": record.get("amount"),
                                "reason": record.get("reason"), "applied": record.get("applied")},
        })
        did = did_text(record.get("action", "NO_CHANGE"), record.get("amount") or 0,
                       bool(record.get("invalid")), bool(record.get("applied")))
        requested = record["replicas_target"] if record.get("applied") else current
        history.append(history_entry(record["snapshot"], requested, did))
    return cases


def read_records(log_path: Path) -> list[dict]:
    records = []
    for line in log_path.read_text(errors="replace").splitlines():
        try:
            records.append(json.loads(line))
        except ValueError:
            continue
    return records


# ---- run: cases x variants x repeats ------------------------------------------------------

def ask_once(client, variant, case: dict) -> dict:
    prompt = variant.build(case["snapshot"], case["current_replicas"], case["min_replicas"],
                           case["max_replicas"], case["history"])
    started = time.perf_counter()
    response = client.complete(variant.system_prompt, prompt, use_cache=False)
    latency = round(time.perf_counter() - started, 2)
    if response is None:
        return {"case": case["id"], "variant": variant.name, "failed": True, "action": None, "amount": None,
                "reason": "model call failed", "raw": "", "latency_s": latency}
    parsed = parse_response(response.text)
    return {"case": case["id"], "variant": variant.name, "failed": False, "invalid": parsed.invalid,
            "action": parsed.action, "amount": parsed.amount, "reason": parsed.reason,
            "raw": response.text, "latency_s": latency}


def run_lab(cases: list[dict], variants: list, repeats: int, client, workers: int = 4) -> list[dict]:
    jobs = [(v, c) for c in cases for v in variants for _ in range(repeats)]
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        return list(pool.map(lambda job: ask_once(client, *job), jobs))


def label(result: dict) -> str:
    if result["failed"]:
        return "CALL FAILED"
    if result.get("invalid"):
        return "INVALID"
    return result["action"] if result["action"] == "NO_CHANGE" else f"{result['action']}+{result['amount']}"


def agreement(results: list[dict], cases: list[dict]) -> dict[str, tuple[int, int]]:
    """variant -> (sensible answers, answers checked), over cases that carry an expectation."""
    expect = {c["id"]: set(c["expect"]) for c in cases if c.get("expect")}
    out: dict[str, list[int]] = {}
    for r in results:
        if r["case"] in expect:
            ok_total = out.setdefault(r["variant"], [0, 0])
            ok_total[1] += 1
            ok_total[0] += int((not r["failed"]) and (not r.get("invalid")) and r["action"] in expect[r["case"]])
    return {k: (v[0], v[1]) for k, v in out.items()}


def report(results: list[dict], cases: list[dict], variants: list) -> str:
    by = {}
    for r in results:
        by.setdefault((r["case"], r["variant"]), []).append(r)
    lines = []
    for case in cases:
        expect = f"  (sensible: {' or '.join(case['expect'])})" if case.get("expect") else ""
        lines.append(f"{case['id']}{expect}")
        for v in variants:
            runs = by.get((case["id"], v.name), [])
            counts = Counter(label(r) for r in runs)
            text = ", ".join(f"{k} x{n}" for k, n in counts.most_common())
            ok = ""
            if case.get("expect"):
                good = sum(1 for r in runs if not r["failed"] and not r.get("invalid") and r["action"] in case["expect"])
                ok = f"   sensible {good}/{len(runs)}"
            lines.append(f"  {v.name:<20} {text}{ok}")
    totals = agreement(results, cases)
    if totals:
        lines += ["", "Agreement with the sensible answers (cases that have an expectation):"]
        for v in variants:
            if v.name in totals:
                good, n = totals[v.name]
                lines.append(f"  {v.name:<20} {good}/{n}  ({100 * good / n:.0f}%)")
    failed = sum(1 for r in results if r["failed"])
    invalid = sum(1 for r in results if r.get("invalid"))
    lines += ["", f"{len(results)} calls, {failed} failed, {invalid} invalid answers."
                  " Few repeats give rough numbers: treat differences of one or two answers as noise."]
    return "\n".join(lines)


# ---- command line --------------------------------------------------------------------------

def cmd_extract(args) -> int:
    cases = cases_from_records(read_records(args.log), args.name)
    if not cases:
        sys.exit(f"No ticks with metrics found in {args.log}")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(cases, indent=1) + "\n")
    print(f"Wrote {len(cases)} cases to {args.out}")
    return 0


def cmd_run(args) -> int:
    cases = [c for path in args.cases for c in json.loads(path.read_text())]
    if args.ticks:
        cases = [c for c in cases if c["tick"] in args.ticks]
    variants = [get_variant(n) for n in (args.variants or list(VARIANTS))]
    if not cases:
        sys.exit("No cases selected (check --cases and --ticks).")
    calls = len(cases) * len(variants) * args.repeats
    print(f"{len(cases)} cases x {len(variants)} variants x {args.repeats} repeats = {calls} model calls")
    if args.dry_run:
        c, v = cases[0], variants[-1]
        print(f"\n--- example: {c['id']} with {v.name} ---\n"
              + v.build(c["snapshot"], c["current_replicas"], c["min_replicas"], c["max_replicas"], c["history"]))
        return 0
    if calls > args.max_calls and not args.yes:
        sys.exit(f"{calls} calls is more than --max-calls {args.max_calls}. Add --yes if you mean it.")

    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env")
    from controllers.llm.main import make_client  # same client (and settings) as the live controller
    client = make_client()
    results = run_lab(cases, variants, args.repeats, client, args.workers)
    print("\n" + report(results, cases, variants))
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps({"model": getattr(client, "model", None), "repeats": args.repeats,
                               "variants": [v.name for v in variants], "results": results}, indent=1))
    print(f"\nEvery raw reply is saved in {out.relative_to(REPO_ROOT)} (not committed).")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    ex = sub.add_parser("extract", help="turn a controller.log into cases")
    ex.add_argument("log", type=Path)
    ex.add_argument("--name", required=True, help="short name for these cases, e.g. gradual_ramp_run1")
    ex.add_argument("--out", type=Path, required=True)
    ex.set_defaults(func=cmd_extract)

    run = sub.add_parser("run", help="ask the prompt variants (live API calls)")
    run.add_argument("--cases", type=Path, nargs="+", required=True)
    run.add_argument("--ticks", type=int, nargs="+", help="only these tick numbers")
    run.add_argument("--variants", nargs="+", help=f"default: all ({', '.join(VARIANTS)})")
    run.add_argument("--repeats", type=int, default=5)
    run.add_argument("--workers", type=int, default=4)
    run.add_argument("--max-calls", type=int, default=200)
    run.add_argument("--yes", action="store_true", help="allow more than --max-calls")
    run.add_argument("--dry-run", action="store_true", help="count the calls and show one prompt; call nothing")
    run.set_defaults(func=cmd_run)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
