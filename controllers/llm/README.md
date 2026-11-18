# llm: our LLM-based controller

**Status: implemented.** `start.sh` runs `python -m controllers.llm.main` until SIGTERM.
Read the contract in [controllers/README.md](../README.md) first.

This controller runs as a **separate process** on your machine for the whole run:

```
controllers/llm/
├── README.md
├── start.sh        # exec python -m controllers.llm.main   (runs until SIGTERM)
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

`llm` and `llm_context` (`SCALE_LLM_MODE`) use that camp prompt. `llm_history`,
`llm_predictive`, and `llm_predictive_context` add trends, a capped request-rate
forecast, and startup awareness. Notes, when the mode includes them, come from
`SCALE_OPERATOR_NOTES`, not from scenario files. `SCALE_LLM_FAKE=1` skips the API.
Missing snapshot fields stay missing (never filled in as zero). There is no memory metric.

After `kubectl` accepts a scale, that target is remembered until a later snapshot
reports the same desired count. A stale desired count cannot scale down over it
or replace it. Confirmed overload can still add pods, using the accepted target
as the baseline. If the desired count does not catch up within
`SCALE_LLM_RECONCILE_TIMEOUT_SECONDS` (default 60), the observed count is adopted.
This process lock does not stop a different controller from scaling the same deployment.

Run one mode through the shared runner (cluster already up, from the repo root):

```text
python experiments/run_experiment.py --controller llm --scenario gradual_ramp --duration 180
```

Set `SCALE_LLM_MODE` to `llm`, `llm_context`, `llm_history`, `llm_predictive`, or
`llm_predictive_context` before that command. Only `llm_context` and
`llm_predictive_context` see `SCALE_OPERATOR_NOTES`. The runner writes
`controller.log` next to `metrics.csv`. An existing `repeat_N` folder is skipped;
pass `--repeat` with the next free number.

Defaults, not tuned: scale-up step 3, scale-down step 1, 120 s before scale-down,
replicas 1–10. Tests: `python -m pytest tests/core/test_llm_cluster_controller.py`.
