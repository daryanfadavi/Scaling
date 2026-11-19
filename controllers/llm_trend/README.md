# llm_trend: LLM controller, prompt variant `cluster-v2-trend`

A process plugin identical to [`llm/`](../llm/README.md) (same model, guardrails, cooldowns and
settings) except for the prompt: the baseline prompt plus a trend line computed from the controller's own recent readings.

It exists so a prompt experiment gets its own results folder
(`experiments/results/<scenario>/llm_trend/`) without changing the runner. The prompt itself is in
[`llm/prompt.py`](../llm/prompt.py); the variant name is logged in the first line of `controller.log`.

```bash
python experiments/run_experiment.py --controller llm_trend --scenario sudden_spike --repeat 3
```
