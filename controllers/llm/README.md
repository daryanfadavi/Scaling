# llm: our LLM-based controller

**Status: implemented.** `start.sh` runs `python -m controllers.llm.main` until SIGTERM.
Read the contract in [controllers/README.md](../README.md) first.

This controller runs as a **separate process** on your machine for the whole run:

```
controllers/llm/
├── README.md
├── start.sh        # sets this folder's mode, then runs until SIGTERM
└── main.py         # loop: observe -> ask the model -> guardrails -> scale
```

Contract details that matter here:
- **Observe** with `from telemetry import latest_snapshot` (one dict per call, fields in
  [docs/telemetry.md](../../docs/telemetry.md)). No PromQL of your own.
- **Act** with `kubectl --context "$KUBE_CONTEXT" -n "$SCALE_NAMESPACE" scale deployment
  "$SCALE_DEPLOYMENT" --replicas=N`, **only after** guardrails: clamp to
  `SCALE_MIN_REPLICAS`..`SCALE_MAX_REPLICAS`, enforce a cooldown, reject unparseable output (AGENTS.md).
- **Stop** cleanly on `SIGTERM` (the runner sends it at the end of every run; `SIGKILL` follows after 20 s).
- **Log** every decision to stdout: it lands in `controller.log` next to the run's `metrics.csv`.
- **API key:** read `ANTHROPIC_API_KEY` (or `OPENROUTER_API_KEY`) from the environment or the gitignored
  `.env`. Never commit it. The runner passes its own environment through to `start.sh`.

The simulator's prompt, parser and guardrails in `controller/llm_controller.py` and
`controller/llm_client.py` are a good starting point, but don't import experiment configs or
scenario details: the controller must not know it's being evaluated.

Missing snapshot fields stay missing (never filled in as zero). There is no memory metric.

After `kubectl` accepts a scale, that target is remembered until a later snapshot
reports the same desired count. A stale desired count cannot scale down over it
or replace it. Confirmed overload can still add pods, using the accepted target
as the baseline. If the desired count does not catch up within
`SCALE_LLM_RECONCILE_TIMEOUT_SECONDS` (default 60), the observed count is adopted.
This process lock does not stop a different controller from scaling the same deployment.

## Modes

Each mode is its own plugin. `start.sh` sets the mode and whether the stand-in model is used, so a value already in the environment cannot change what gets recorded. The results folder is the plugin name: `experiments/results/<scenario>/<plugin>/`.

| Run with `--controller` | What the model sees |
|---|---|
| `llm` | Current measurements |
| `llm_context` | Current measurements, plus notes from `SCALE_OPERATOR_NOTES` |
| `llm_history` | Recent trends |
| `llm_predictive` | Trends and a capped request-rate forecast |
| `llm_predictive_context` | That forecast, plus the same notes |
| `llm_fake` | Same as `llm`, with a stand-in model and no API key |

```text
python experiments/run_experiment.py --controller llm_predictive --scenario gradual_ramp --duration 180
```

Only `llm_context` and `llm_predictive_context` pass `SCALE_OPERATOR_NOTES` through. Those notes are typed in by hand. They are not read from scenario files. The runner writes `controller.log` next to `metrics.csv`. An existing `repeat_N` folder is skipped; pass `--repeat` with the next free number.

## Settings compared with HPA

| | This controller | HPA (`controllers/hpa`) |
|---|---|---|
| Scale up | At most +3 replicas per 15 s step | At most +4 pods or +100% per 15 s, whichever is larger |
| Scale down wait | 120 s of low demand | 300 s stabilization window |
| Scale down size | 1 replica at a time, then 30 s before another | Up to −100% per 15 s after the wait |
| Replicas | 1 to 10 | 1 to 10 |

These are design choices, not tuned results. They change how many replicas get used, so a cost comparison with HPA has to state them.

Tests: `python -m pytest tests/core/test_llm_cluster_controller.py`.
