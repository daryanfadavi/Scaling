# LLM-Enhanced Adaptive Cloud Autoscaling

This project investigates whether large language models can act as effective decision-making agents for cloud resource autoscaling, and — more importantly — *when and why* they help or fail to help compared with conventional autoscaling approaches under dynamic workloads.

## New team members - Start here

Read these in order:

1. **[Project overview](docs/onboarding/0_readme.md)** — what the project is, the research question, timeline
2. **[Repo structure](docs/onboarding/repo.md)** — how the code and docs are organized
3. **[Dev environment setup](docs/onboarding/setup.md)** — get Docker, Kubernetes, and the rest running locally
4. **[Related work](docs/onboarding/related_works.md)** — the relevant papers, to be updated throughout the project
5. **[Contributing](CONTRIBUTING.md)** — how we branch, review, and commit
6. **[Camp QMIND build](docs/onboarding/camp_qmind.md)** — the simulator, the controller race, and the pair tasks

If you're using an AI coding agent in this repo, it should read **[AGENTS.md](AGENTS.md)** first.

## Where we are

**Camp QMIND (Oct 3–4):** a small, deterministic Python simulator (`simulator/`) where we race an HPA baseline, a metric-only LLM controller, and an LLM + operator-context controller on identical workloads. See **[camp_qmind.md](docs/onboarding/camp_qmind.md)**. The simulator is a pilot for deciding what to test on real infrastructure.

**Phase 1 (in parallel):** local Kubernetes prototype: a containerized service, Locust-generated workloads, and Kubernetes HPA as our first working baseline. Once that loop runs end-to-end, controllers proven out in the simulator move onto the cluster for comparison.

See [0_readme.md](docs/onboarding/0_readme.md) for the full phase breakdown.

---
QMIND, Queen's University · 2026–2027
