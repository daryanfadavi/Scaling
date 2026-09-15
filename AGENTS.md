# Instructions for AI coding agents

This file is for tools like Claude Code operating directly in this repository. If you're a human, see [README.md](README.md) and [docs/onboarding/](docs/onboarding/0_readme.md) instead.

## Context

SCALE is a research project (not a product), comparing LLM-based cloud autoscaling controllers against conventional baselines (primarily Kubernetes HPA). Correctness and reproducibility of experiments matter more than feature velocity. See `docs/onboarding/0_readme.md` for full research context before making non-trivial changes.

## Repo layout

See `docs/onboarding/repo.md` for the full breakdown. In short: `cloud-env/` is the test environment, `controller/` is decision-making logic, `experiments/` runs controllers against workloads and records results, `paper/` is the write-up.

## Ground rules

- **Never commit secrets, kubeconfig files, cloud credentials, or API keys.** Check `.gitignore` covers anything you generate; if it doesn't, add it before committing.
- **Don't merge to `main` directly.** Work on a branch and open a PR, per [CONTRIBUTING.md](CONTRIBUTING.md).
- **Controllers must not know they're being evaluated.** Keep `controller/` logic free of anything that references experiment configs, baselines, or expected outcomes — that coupling would invalidate the comparison.
- **LLM controller action spaces must stay bounded and validated.** Any LLM-generated scaling decision needs to pass through guardrail validation (min/max replicas, cooldown periods) before being applied — never call the Kubernetes API directly from raw LLM output.
- **Prefer explicit, inspectable code over clever abstraction**, especially in `controller/` and `experiments/` — other (non-expert) team members need to be able to read and modify this.
- **Run existing tests before proposing a commit**, and add tests for new controller logic where feasible.

## When you're unsure

If a task is ambiguous — e.g., which folder new code belongs in, whether a new dependency is worth adding, or whether an experimental change affects fairness of comparison — flag it in the PR description rather than guessing silently.
