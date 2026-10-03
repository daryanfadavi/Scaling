# experiments/

Experiment configs, run scripts, and results — kept separate from `controller/` logic itself.

```
experiments/
├── configs/     # scenario YAMLs: workload shape, operator notes, env overrides
├── run.py       # python -m experiments.run --controller hpa --scenario spike [--seed N] [--repeats N] [--offline] [--label X]
├── plot.py      # python -m experiments.plot  -> per-scenario time series + leaderboard
├── results/     # per-run CSV/JSON/PNG, plots/, leaderboard.md (gitignored)
└── cache/       # cached LLM responses (gitignored)
```

Which controller, seed and repeat count are CLI arguments (recorded in each run's JSON summary along with the git commit and full simulator config), so any result can be re-run exactly. See [camp_qmind.md](../docs/onboarding/camp_qmind.md).

Before adding a new experiment, open an issue describing the question it answers and what would count as a meaningful result either way — see [CONTRIBUTING.md](../CONTRIBUTING.md).
