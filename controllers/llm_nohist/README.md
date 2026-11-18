# llm_nohist: LLM controller, prompt variant `cluster-v1-nohist`

A process plugin identical to [`llm/`](../llm/README.md) (same model, guardrails, cooldowns and
settings) except for the prompt: the baseline prompt with no past decisions shown to the model (history off).

It exists so a prompt experiment gets its own results folder
(`experiments/results/<scenario>/llm_nohist/`) without changing the runner. The prompt itself is in
[`llm/prompt.py`](../llm/prompt.py); the variant name is logged in the first line of `controller.log`.

```bash
python experiments/run_experiment.py --controller llm_nohist --scenario sudden_spike --repeat 3
```
