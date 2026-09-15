# SCALE — LLM-Enhanced Adaptive Cloud Autoscaling

SCALE investigates whether large language models can act as effective decision-making agents for cloud resource autoscaling, and — more importantly — *when and why* they help or fail to help compared with conventional autoscaling approaches (like Kubernetes' Horizontal Pod Autoscaler) under dynamic workloads.

This is a QMIND undergraduate research project at Queen's University (2026–2027), aimed at a submittable research paper, not just a working demo.

## New to the team? Start here

Read these in order:

1. **[Project overview](docs/onboarding/0_readme.md)** — what SCALE is, the research question, our advisor situation, and the roadmap
2. **[Repo structure](docs/onboarding/repo.md)** — how the code and docs are organized
3. **[Dev environment setup](docs/onboarding/setup.md)** — get Docker, Kubernetes, and the rest running locally
4. **[Related work](docs/onboarding/related_works.md)** — the small set of papers worth reading before you start building
5. **[Contributing](CONTRIBUTING.md)** — how we branch, review, and commit

If you're using an AI coding agent (e.g. Claude Code) in this repo, it should read **[AGENTS.md](AGENTS.md)** first.

## Where we are

**Phase 1 (current):** local Kubernetes prototype — a containerized service, Locust-generated workloads, and Kubernetes HPA as our first working baseline. Once that loop runs end-to-end, we replace HPA with our own LLM-based controller and start comparing.

See [0_readme.md](docs/onboarding/0_readme.md) for the full phase breakdown.

---
QMIND, Queen's University · 2026–2027
