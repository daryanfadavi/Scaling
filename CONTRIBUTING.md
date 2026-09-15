# Contributing to SCALE

## Branching

- `main` is protected — no direct commits. It should always be in a state that runs.
- Branch per task: `feature/<short-description>`, `fix/<short-description>`, or `experiment/<short-description>` (e.g. `experiment/llm-contextual-vs-hpa`).
- Open a PR into `main` when ready for review. At least one other team member should review before merging.

## Commit messages

Keep them short and specific — describe *what changed*, not *what you did*:

```
Good:  add HPA stabilization window config to k8s manifests
Bad:   update files
```

If a commit is experiment-related, prefix with the experiment name where useful, e.g. `experiment/llm-contextual: fix prompt template bug`.

## Pull requests

Include in the PR description:
- What this changes and why
- If it's a new top-level folder or major structural change, say so explicitly (see [repo.md](docs/onboarding/repo.md))
- If it affects an experiment, what you expect it to change about the results

## Code style

- Python: format with `black`, lint with `ruff`. Type hints where they help readability, not everywhere for its own sake.
- Keep controller logic (`controller/`) and experiment orchestration (`experiments/`) separate — a controller shouldn't know it's being benchmarked.

## Proposing a new experiment

Before running a new experimental variant, briefly describe in an issue:
- What question it answers
- What the baseline/comparison is
- What would count as a meaningful result either way (including a negative one)

This isn't bureaucracy for its own sake — it's what keeps our eventual results defensible when someone asks "how do you know this comparison was fair?"

## Using AI coding agents

If you're using Claude Code or a similar agent to help write code in this repo, have it read [AGENTS.md](AGENTS.md) first. Review agent-generated diffs before committing — you're responsible for what lands in `main`, not the agent.
