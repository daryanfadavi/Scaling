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
| Temperature | not sent (the Anthropic API rejects it for Haiku 4.5, so the model's default applies); logged in the first line of `controller.log` |
| Decision interval | 15 s (one per telemetry snapshot) |
| Action | `SCALE_UP` / `SCALE_DOWN` by 1 to 3 pods, or `NO_CHANGE` |
| Replicas | 1 to 10 |
| Cooldown after any scale | 30 s: no further scaling for 2 ticks |
| Scale-down cooldown | 120 s after any scale (HPA and KEDA use 300 s; shorter on purpose: change it only for all runs) |
| History in the prompt | last 5 decisions, with what happened to each |
| Prompt version | `cluster-v1` by default (`controllers/llm/prompt.py`). Variants are chosen with `SCALE_PROMPT` (see below) and the name is logged in the first line of `controller.log` |
| Goal given to the model | p95 latency under 300 ms, error rate near zero, fewest pods possible |

**What the model is told about the service:** each pod requests 100m CPU and can burst to 200m, CPU %
is relative to the request (so it can exceed 100), pods take a while to become ready, and metrics are
1-minute averages that lag. It is **not** told per-pod capacity (req/s per pod), because that depends on
the machine (see the calibration note in [keda/README.md](../keda/README.md)); it has to infer it from CPU
and request rate. It never sees the scenario, the planned load, or the other controllers.

## Prompt variants

For prompt experiments, `prompt.py` holds named variants. Each differs from the baseline `cluster-v1` by one thing,
and `tests/core/test_prompt_variants.py` checks that. Choose one with `SCALE_PROMPT=<name>`. The plugin folders
below just set it, so each variant's runs land in their own results folder (the runner has no `--label`).

| Variant | Plugin folder | Change from `cluster-v1` |
|---|---|---|
| `cluster-v1` | `llm/` | none: the baseline |
| `cluster-v1-nohist` | `llm_nohist/` | the model sees no past decisions |
| `cluster-v2-trend` | `llm_trend/` | adds a trend line (rising / falling / flat) computed from the controller's own recent readings, plus one system-prompt sentence |
| `cluster-v2-words` | `llm_words/` | adds a word (low / high / ...) next to CPU, latency and errors; the thresholds are first guesses |

Everything else (model, cooldowns, bounds, 5-decision history) is identical, so differences in results come from the prompt.
A new variant needs a new name and an entry in `VARIANTS`. Nothing in a prompt may mention the experiment.
The runner hashes only the plugin folder, so `metadata.json` also records the git commit and whether the tree was dirty: run
experiments from a clean, committed tree.

To compare prompts without a cluster, use `experiments/prompt_lab.py`; to score cluster runs, use
`experiments/summarize_cluster.py` (both in [experiments/README.md](../../experiments/README.md)).

## Running it

Needs the cluster from `scripts/setup_cluster.sh` and a key in `.env` (see `.env.example`; never commit it).

```bash
python experiments/run_experiment.py --controller llm --scenario gradual_ramp --duration 180
```

Check `experiments/results/gradual_ramp/llm/repeat_1/controller.log` for the decisions and `metrics.csv`
for `replicas_desired` changing. `start.sh` uses `python` from your `PATH`, so activate the venv first.

A run makes about duration/15 live API calls (40 for 10 minutes, plus the settle time). Model output isn't
repeatable (temperature can't be set to 0 for this model), so use `--repeat` for comparisons.

Tests (no cluster or key needed): `pytest tests/core/test_cluster_llm.py tests/core/test_prompt_variants.py`.
