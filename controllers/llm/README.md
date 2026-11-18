# llm: our LLM-based controller

A **process plugin**: the runner starts `start.sh` after the run's setup and stops it with
`SIGTERM` at the end (contract: [controllers/README.md](../README.md)). Decisions are printed
as one JSON line per tick to stdout, which lands in `controller.log` next to `metrics.csv`.

```
controllers/llm/
├── README.md
├── start.sh     # exec python -m controllers.llm.main
├── main.py      # loop: observe -> ask the model -> parse -> guardrails -> kubectl scale
└── prompt.py    # system prompt + how a snapshot becomes the user message (PROMPT_VERSION)
```

## What happens every tick

1. **Observe:** `telemetry.latest_snapshot()` (fields in [docs/telemetry.md](../../docs/telemetry.md)).
   No PromQL of our own. The Deployment's current `spec.replicas` is read with `kubectl`, because the
   snapshot can still show the value from before our own last scale.
2. **Ask:** one prompt to the model through `controller/llm_client.py` (live call, no cache).
3. **Parse:** `controller.llm_controller.parse_response`, the same parser as the simulator controller.
   Anything that isn't `{"action": SCALE_UP|SCALE_DOWN|NO_CHANGE, "amount": 1-3, "reason": ...}` becomes
   `NO_CHANGE`.
4. **Guardrails** (`apply_guardrails` in `main.py`): clamp to 1-10 replicas, then the cooldowns below.
   `scale_to()` checks the bounds a second time, and is the only code that touches the Kubernetes API.
5. **Act:** `kubectl scale` only if the guarded target differs from the current count.

If Prometheus, kubectl or the model call fails, the tick holds (no change) and logs why.

## Settings

| Setting | Value |
|---|---|
| Model | `SCALE_MODEL` from `.env`, default `claude-haiku-4-5-20251001` (`SCALE_PROVIDER=openrouter` uses `OPENROUTER_MODEL`; pin `OPENROUTER_UPSTREAM`). The model is logged in the first line of `controller.log`. |
| Temperature | 0 |
| Decision interval | 15 s (one per telemetry snapshot) |
| Action | `SCALE_UP` / `SCALE_DOWN` by 1 to 3 pods, or `NO_CHANGE` |
| Replicas | 1 to 10 |
| Cooldown after any scale | 30 s: no further scaling for 2 ticks |
| Scale-down cooldown | 120 s after any scale (HPA and KEDA use 300 s; shorter on purpose: change it only for all runs) |
| History in the prompt | last 5 decisions, with what happened to each |
| Prompt version | `cluster-v1` (`controllers/llm/prompt.py`) |
| Goal given to the model | p95 latency under 300 ms, error rate near zero, fewest pods possible |

**What the model is told about the service:** each pod requests 100m CPU and can burst to 500m, CPU %
is relative to the request (so it can exceed 100), pods take a while to become ready, and metrics are
1-minute averages that lag. It is **not** told per-pod capacity (req/s per pod), because that depends on
the machine (see the calibration note in [keda/README.md](../keda/README.md)); it has to infer it from CPU
and request rate. It never sees the scenario, the planned load, or the other controllers.

## Running it

Needs the cluster from `scripts/setup_cluster.sh` and a key in `.env` (see `.env.example`; never commit it).

```bash
python experiments/run_experiment.py --controller llm --scenario gradual_ramp --duration 180
```

Check `experiments/results/gradual_ramp/llm/repeat_1/controller.log` for the decisions and `metrics.csv`
for `replicas_desired` changing. `start.sh` uses `python` from your `PATH`, so activate the venv first.

A run makes about duration/15 live API calls (40 for 10 minutes, plus the settle time). Model output isn't
perfectly repeatable even at temperature 0, so use `--repeat` for comparisons.

Tests (no cluster or key needed): `pytest tests/core/test_cluster_llm.py`.
