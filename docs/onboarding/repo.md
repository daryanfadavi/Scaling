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
│   │   └── related_works.md ← papers to read before you start
│   └── assets/                ← images/diagrams used in docs
├── cloud-env/                 ← Phase 1: containerized app, k8s manifests, Locust workload scripts
├── controller/                 ← Phase 2: LLM-based and baseline controllers
├── experiments/                ← experiment configs, run scripts, logged results
└── paper/                       ← the write-up (LaTeX/markdown, figures, notes)
```

## Where things live as the project grows

- **`cloud-env/`** — everything needed to stand up the environment we're testing against: the Dockerfile for our synthetic service, Kubernetes manifests (Deployment, Service, HPA config), and Locust workload definitions (constant load, ramp, spike, bursty, etc.).
- **`controller/`** — the actual decision-making logic. Expect subfolders per controller type as they're built out, e.g. `controller/hpa_baseline/`, `controller/llm_metric_only/`, `controller/llm_contextual/`, `controller/hierarchical/`.
- **`experiments/`** — anything that runs a controller against a workload and records results. Keep experiment configs (which controller, which workload, how many repeats) separate from the raw results/logs they produce, so runs are reproducible.
- **`paper/`** — this is a research project, not just a codebase. As results come in, this is where write-up, figures, and draft sections live.

## A note on structure as we go

This layout is a starting skeleton, not a fixed contract. As the LLM controller design solidifies (state representation, prompt format, action space, etc.), expect `controller/` in particular to grow real internal structure. If you're about to create a new top-level folder, mention it in your PR description so the rest of the team knows it exists — see [CONTRIBUTING.md](../../CONTRIBUTING.md).
