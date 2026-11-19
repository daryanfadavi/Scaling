# llm_words: LLM controller, prompt variant `cluster-v2-words`

A process plugin identical to [`llm/`](../llm/README.md) (same model, guardrails, cooldowns and
settings) except for the prompt: the baseline prompt plus a word (low / high / ...) next to CPU, latency and errors.

It exists so a prompt experiment gets its own results folder
(`experiments/results/<scenario>/llm_words/`) without changing the runner. The prompt itself is in
[`llm/prompt.py`](../llm/prompt.py); the variant name is logged in the first line of `controller.log`.

```bash
python experiments/run_experiment.py --controller llm_words --scenario sudden_spike --repeat 3
```
