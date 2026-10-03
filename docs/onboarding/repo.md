# How the repo is organized

```
SCALE/
├── README.md                 ← start here
├── CONTRIBUTING.md           ← branching, PRs, commit style
├── AGENTS.md                 ← instructions for AI coding agents (Claude Code, etc.)
├── LICENSE
├── docs/
│   ├── onboarding/           ← you are here
│   │   ├── 0_readme.md       ← project overview, research question, roadmap
│   │   ├── repo.md           ← this file
│   │   ├── setup.md          ← dev environment setup
│   │   ├── related_works.md  ← papers to read before you start
│   │   └── camp_qmind.md     ← simulator + controller race (start here for Camp QMIND)
│   └── assets/                ← images/diagrams used in docs
├── cloud-env/                 ← Phase 1: containerized app, k8s manifests, Locust workload scripts
├── simulator/                 ← simulated cluster: workloads, latency model, metrics (no k8s needed)
├── controller/                ← LLM-based and baseline controllers (shared by simulator and, later, k8s)
├── experiments/               ← experiment configs, run scripts, plots; results/ and cache/ are gitignored
├── tests/                     ← pytest: core/ must pass; todo/ is the spec for unfinished controllers
├── scripts/                   ← small utilities (check_api.py: test your Anthropic API key)
├── paper/                     ← the write-up (LaTeX/markdown, figures, notes)
├── requirements.txt           ← Python dependencies
└── .env.example               ← copy to .env (gitignored) and add your API key
```

## Where things live as the project grows

- **`cloud-env/`** — everything needed to stand up the environment we're testing against: the Dockerfile for our synthetic service, Kubernetes manifests (Deployment, Service, HPA config), and Locust workload definitions (constant load, ramp, spike, bursty, etc.).
- **`simulator/`** — a deliberately simple, deterministic stand-in for a cluster (added for Camp QMIND). `env.py` holds replicas, startup delay, backlog and env-level guardrails; `workloads.py` holds load shapes; `latency.py` the queueing model; `metrics.py` the per-run summary. Same seed means the same trace for every controller. It's a pilot for choosing experiments, not a substitute for the real cluster.
- **`controller/`** — the actual decision-making logic. Right now it's one file per controller: `base.py` (the `Observation`/`Decision`/`Controller` interface), `static.py` (reference example), `human.py`, `hpa_baseline.py`, `llm_controller.py` (metric-only and with-context variants via flags), `scheduled_baseline.py`, and `llm_client.py` (Anthropic API wrapper with caching). Controllers only see an `Observation`, never scenario configs. Expect subfolders once controllers outgrow single files (e.g. a `hierarchical/` planner + low-level controller).
- **`experiments/`** — anything that runs a controller against a workload and records results. Keep experiment configs (which controller, which workload, how many repeats) separate from the raw results/logs they produce, so runs are reproducible. Today: `configs/*.yaml` (scenarios), `run.py` (`python -m experiments.run ...`), `plot.py` (time-series + leaderboard). Output goes to `results/` and LLM responses to `cache/`, both gitignored.
- **`tests/`** — `tests/core/` must always pass (`pytest`). `tests/todo/` is marked `todo` and excluded by default; it specifies controllers that aren't implemented yet (`pytest -m todo`).
- **`paper/`** — this is a research project, not just a codebase. As results come in, this is where write-up, figures, and draft sections live.

## A note on structure as we go

This layout is a starting skeleton, not a fixed contract. As the LLM controller design solidifies (state representation, prompt format, action space, etc.), expect `controller/` in particular to grow real internal structure. If you're about to create a new top-level folder, mention it in your PR description so the rest of the team knows it exists — see [CONTRIBUTING.md](../../CONTRIBUTING.md).
