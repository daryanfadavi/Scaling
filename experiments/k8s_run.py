"""Run the camp LLM controller against the live scale-app Deployment.

    python -m experiments.k8s_run --controller llm --disable-hpa --ticks 40 --fake-llm
    python -m experiments.k8s_run --controller llm_context --disable-hpa --scenario spike_with_warning --fake-llm

This process is the scaler. Pass --disable-hpa so the cluster HPA does not
fight it. Controllers stay cluster-blind. A real (non-fake) run needs
ANTHROPIC_API_KEY in .env.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import subprocess
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from controller.base import Controller, Decision, Observation
from controller.fake_llm_client import ClusterLLMController, FakeLLMClient
from controller.llm_client import LLMClient
from simulator.config import SimConfig
from simulator.env import apply_env_guardrails

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = REPO_ROOT / "experiments" / "results" / "k8s"
CACHE_DIR = REPO_ROOT / "experiments" / "cache"
CPU_REQUEST_M = 100.0  # matches cloud-env/k8s/deployment.yaml
K8S_ENV = SimConfig(min_replicas=1, max_replicas=10, max_step=4, initial_replicas=1)

SCENARIOS = {
    "ramp": {"ticks": 40, "interval_s": 15, "notes": []},
    "spike": {"ticks": 40, "interval_s": 15, "notes": []},
    "spike_with_warning": {
        "ticks": 40,
        "interval_s": 15,
        "notes": [
            {
                "start_tick": 2,
                "end_tick": 4,
                "text": "Marketing campaign launches in ~30 seconds; expect a large traffic jump.",
            }
        ],
    },
}


class KubectlError(RuntimeError):
    pass


def parse_cpu_to_millicores(value: str) -> float:
    text = (value or "0").strip()
    if text.endswith("n"):
        return float(text[:-1]) / 1_000_000.0
    if text.endswith("u"):
        return float(text[:-1]) / 1_000.0
    if text.endswith("m"):
        return float(text[:-1])
    return float(text) * 1000.0


def replica_counts_from_deployment(doc: dict[str, Any]) -> tuple[int, int, int]:
    desired = int((doc.get("spec") or {}).get("replicas") or 0)
    ready = int((doc.get("status") or {}).get("readyReplicas") or 0)
    return ready, max(0, desired - ready), desired


def millicores_from_pod_metrics(metrics_doc: dict[str, Any], prefix: str = "scale-app") -> list[float]:
    used = []
    for item in metrics_doc.get("items") or []:
        name = str((item.get("metadata") or {}).get("name") or "")
        if not name.startswith(prefix):
            continue
        total = 0.0
        for container in item.get("containers") or []:
            total += parse_cpu_to_millicores(str((container.get("usage") or {}).get("cpu") or "0"))
        used.append(total)
    return used


def cpu_pct_from_millicores(millicores: list[float], request_m: float = CPU_REQUEST_M) -> float:
    if not millicores or request_m <= 0:
        return 0.0
    return sum(100.0 * m / request_m for m in millicores) / len(millicores)


def aggregate_app_metrics(snapshots: list[dict[str, Any]]) -> tuple[float, float, float]:
    if not snapshots:
        return 0.0, 0.0, 0.0
    rate = sum(float(s.get("request_rate") or 0) for s in snapshots)
    backlog = sum(float(s.get("backlog") or 0) for s in snapshots)
    weights = [float(s.get("requests_in_window") or 0) for s in snapshots]
    total = sum(weights)
    latency = (
        sum(float(s.get("latency_ms") or 0) * w for s, w in zip(snapshots, weights)) / total
        if total
        else 0.0
    )
    return rate, latency, backlog


def make_cluster_observation(
    tick: int,
    ready: int,
    pending: int,
    cpu_pct: float,
    request_rate: float = 0.0,
    latency_ms: float = 0.0,
    backlog: float = 0.0,
    min_replicas: int = 1,
    max_replicas: int = 10,
    operator_notes: list[str] | None = None,
    history: list[dict[str, Any]] | None = None,
    tick_seconds: float = 15.0,
) -> Observation:
    return Observation(
        tick=tick,
        time_s=tick * tick_seconds,
        replicas_ready=ready,
        replicas_pending=pending,
        cpu_pct=cpu_pct,
        request_rate=request_rate,
        latency_ms=latency_ms,
        backlog=backlog,
        min_replicas=min_replicas,
        max_replicas=max_replicas,
        operator_notes=list(operator_notes or []),
        history=list(history or []),
    )


class Cluster:
    def __init__(self, namespace: str = "default", deployment: str = "scale-app"):
        self.namespace = namespace
        self.deployment = deployment

    def _run(self, args: list[str]) -> str:
        cmd = ["kubectl", *args]
        try:
            proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
        except OSError as exc:
            raise KubectlError(f"could not run kubectl: {exc}") from exc
        if proc.returncode != 0:
            raise KubectlError(f"{' '.join(cmd)} failed: {(proc.stderr or proc.stdout or '').strip()}")
        return proc.stdout

    def _json(self, args: list[str]) -> Any:
        try:
            return json.loads(self._run([*args, "-o", "json"]))
        except json.JSONDecodeError as exc:
            raise KubectlError(f"kubectl did not return JSON: {exc}") from exc

    def replica_counts(self) -> tuple[int, int, int]:
        return replica_counts_from_deployment(
            self._json(["-n", self.namespace, "get", "deployment", self.deployment])
        )

    def cpu_pct(self) -> float:
        path = f"/apis/metrics.k8s.io/v1beta1/namespaces/{self.namespace}/pods"
        doc = json.loads(self._run(["get", "--raw", path]))
        return cpu_pct_from_millicores(
            millicores_from_pod_metrics(doc, prefix=self.deployment)
        )

    def hpa_exists(self) -> bool:
        proc = subprocess.run(
            ["kubectl", "-n", self.namespace, "get", "hpa", self.deployment],
            check=False, capture_output=True, text=True,
        )
        return proc.returncode == 0

    def disable_hpa(self) -> None:
        self._run(["-n", self.namespace, "delete", "hpa", self.deployment, "--ignore-not-found"])

    def set_replicas(self, replicas: int) -> None:
        self._run(
            ["-n", self.namespace, "scale", f"deployment/{self.deployment}", f"--replicas={int(replicas)}"]
        )

    def app_metrics(self) -> tuple[float, float, float]:
        pods = self._json(["-n", self.namespace, "get", "pods", "-l", f"app={self.deployment}"])
        snapshots = []
        py = "import urllib.request as u; print(u.urlopen('http://127.0.0.1:8000/metrics').read().decode())"
        for item in pods.get("items") or []:
            if (item.get("status") or {}).get("phase") != "Running":
                continue
            name = str((item.get("metadata") or {}).get("name") or "")
            if not name:
                continue
            try:
                snapshots.append(json.loads(self._run(["-n", self.namespace, "exec", name, "--", "python", "-c", py])))
            except (KubectlError, json.JSONDecodeError):
                continue
        return aggregate_app_metrics(snapshots)


def notes_for_tick(notes: list[dict[str, Any]], tick: int) -> list[str]:
    out = []
    for note in notes:
        start = int(note.get("start_tick", 0))
        end = int(note.get("end_tick", start + 1))
        if start <= tick < end and note.get("text"):
            out.append(str(note["text"]))
    return out


def apply_decision(cluster: Cluster, decision: Decision, current_total: int, cfg: SimConfig) -> tuple[int, str]:
    applied, note = apply_env_guardrails(decision.target_replicas, current_total, cfg)
    if applied != current_total:
        cluster.set_replicas(applied)
    return applied, note


def make_controller(args: argparse.Namespace) -> Controller:
    client = FakeLLMClient() if args.fake_llm else LLMClient(
        model=args.model, cache_dir=args.cache_dir, offline=args.offline, repeat_index=0
    )
    return ClusterLLMController(
        client,
        use_context=(args.controller == "llm_context"),
        use_history=not args.no_history,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--controller", required=True, choices=["llm", "llm_context"])
    parser.add_argument("--scenario", choices=list(SCENARIOS), help="sets ticks/interval/notes")
    parser.add_argument("--namespace", default="default")
    parser.add_argument("--deployment", default="scale-app")
    parser.add_argument("--interval", type=float, default=None)
    parser.add_argument("--ticks", type=int, default=None)
    parser.add_argument("--disable-hpa", action="store_true")
    parser.add_argument("--note", action="append", default=[])
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--fake-llm", action="store_true")
    parser.add_argument("--model")
    parser.add_argument("--no-history", action="store_true")
    parser.add_argument("--cache-dir", type=Path, default=CACHE_DIR)
    parser.add_argument("--results-dir", type=Path, default=RESULTS_DIR)
    parser.add_argument("-v", action="store_true", dest="verbose")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")

    scenario = dict(SCENARIOS[args.scenario]) if args.scenario else {}
    scenario_name = args.scenario or "adhoc"
    ticks = args.ticks if args.ticks is not None else int(scenario.get("ticks") or 0)
    interval = args.interval if args.interval is not None else float(scenario.get("interval_s") or 15)
    scenario_notes = list(scenario.get("notes") or [])

    cluster = Cluster(namespace=args.namespace, deployment=args.deployment)
    if cluster.hpa_exists():
        if not args.disable_hpa:
            raise SystemExit("Kubernetes HPA is still on; re-run with --disable-hpa")
        cluster.disable_hpa()
        print("deleted Kubernetes HPA; this process is the only scaler")

    cfg = K8S_ENV
    controller = make_controller(args)
    controller.reset()
    run_name = controller.name
    if getattr(controller.client, "provider_name", "") == "fake":
        run_name = f"{controller.name}-fake"
    out_dir = args.results_dir / scenario_name / run_name
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.csv"

    history: deque[dict[str, Any]] = deque(maxlen=cfg.history_length)
    tick = 0
    print(f"controller={run_name}  scenario={scenario_name}  csv={csv_path}")
    print("Ctrl+C to stop" if ticks <= 0 else f"running {ticks} ticks")
    fieldnames = [
        "tick", "time_s", "cpu_pct", "request_rate", "latency_ms", "backlog",
        "replicas_ready", "replicas_pending", "requested", "applied",
        "clamp_note", "reason", "invalid", "operator_notes",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        try:
            while ticks <= 0 or tick < ticks:
                notes = notes_for_tick(scenario_notes, tick) + list(args.note)
                ready, pending, _desired = cluster.replica_counts()
                try:
                    cpu_pct = cluster.cpu_pct()
                except (KubectlError, json.JSONDecodeError) as exc:
                    log.warning("cpu scrape failed (%s); using 0", exc)
                    cpu_pct = 0.0
                try:
                    request_rate, latency_ms, backlog = cluster.app_metrics()
                except KubectlError as exc:
                    log.warning("app /metrics scrape failed (%s)", exc)
                    request_rate, latency_ms, backlog = 0.0, 0.0, 0.0
                obs = make_cluster_observation(
                    tick, ready, pending, cpu_pct,
                    request_rate=request_rate, latency_ms=latency_ms, backlog=backlog,
                    min_replicas=cfg.min_replicas, max_replicas=cfg.max_replicas,
                    operator_notes=notes, history=list(history),
                    tick_seconds=cfg.tick_seconds,
                )
                decision = controller.decide(obs)
                applied, clamp_note = apply_decision(cluster, decision, obs.replicas_total, cfg)
                writer.writerow({
                    "tick": tick, "time_s": obs.time_s,
                    "cpu_pct": round(obs.cpu_pct, 2),
                    "request_rate": round(obs.request_rate, 2),
                    "latency_ms": round(obs.latency_ms, 2),
                    "backlog": round(obs.backlog, 2),
                    "replicas_ready": ready, "replicas_pending": pending,
                    "requested": decision.target_replicas, "applied": applied,
                    "clamp_note": clamp_note,
                    "reason": str(decision.info.get("reason", "")),
                    "invalid": bool(decision.info.get("invalid", False)),
                    "operator_notes": " | ".join(obs.operator_notes),
                })
                fh.flush()
                print(
                    f"tick {tick:3d}  cpu {obs.cpu_pct:6.1f}%  "
                    f"{obs.request_rate:5.1f} req/s  lat {obs.latency_ms:6.0f} ms  "
                    f"ready {ready}  want {decision.target_replicas} -> {applied}"
                )
                history.append({
                    "tick": tick, "replicas_ready": ready, "replicas_pending": pending,
                    "cpu_pct": obs.cpu_pct, "request_rate": obs.request_rate,
                    "latency_ms": obs.latency_ms, "backlog": obs.backlog,
                    "target": decision.target_replicas, "applied_target": applied,
                })
                tick += 1
                if ticks <= 0 or tick < ticks:
                    time.sleep(interval)
        except KeyboardInterrupt:
            print("\nstopped")
    print(f"wrote {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
