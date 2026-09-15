# experiments/

Experiment configs, run scripts, and results — kept separate from `controller/` logic itself.

Planned structure:

```
experiments/
├── configs/     # which controller, which workload, how many repeats, random seeds
├── runs/        # scripts that execute an experiment end-to-end
└── results/     # logged output (gitignored — see .gitignore; decide per-experiment whether raw logs belong in git or elsewhere)
```

Before adding a new experiment, open an issue describing the question it answers and what would count as a meaningful result either way — see [CONTRIBUTING.md](../CONTRIBUTING.md).
