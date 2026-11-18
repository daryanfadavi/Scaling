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

Defaults, not tuned: scale-up step 3, scale-down step 1, 120 s before scale-down,
replicas 1–10. Tests: `python -m pytest tests/core/test_llm_cluster_controller.py`.
