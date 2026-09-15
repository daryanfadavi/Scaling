# What is SCALE?

Cloud platforms need to add or remove compute resources as workload changes — this is called **autoscaling**. Most production systems today do this with thresholds (e.g. "scale up if CPU > 70%") or control-theoretic policies like Kubernetes' Horizontal Pod Autoscaler (HPA). These work well under predictable conditions but struggle to use *contextual* information — things a human operator knows but a metric doesn't capture.

**Example:** CPU and memory both look normal right now. But a marketing campaign starts in 10 minutes and traffic is expected to jump 5x. A threshold-based autoscaler will do nothing until it's already too late. A system that can reason over natural-language context — "a campaign is about to launch" — could scale up preemptively.

SCALE asks: **can large language models function as effective decision-making agents for cloud autoscaling, and how do they compare with conventional approaches under dynamic workloads?**

We are explicitly *not* assuming the LLM wins. A valid, publishable result is "HPA outperforms the LLM in most conditions, except X" — the interesting question is *when and why* LLM-based reasoning helps, not whether it "works" in some general sense.

## Why does this matter?

Learned resource management has mostly meant reinforcement learning so far (see [DeepRM](related_works.md#deeprm), one of our key reference points). RL can learn strong policies, but it typically needs environment interaction, reward engineering, and retraining when objectives change. Our central hypothesis is that a pretrained LLM can incorporate qualitative, changing operational context — budget limits, priority shifts, scheduled events — directly through prompting, without retraining. That's the strongest, most testable claim in this project, and most of our experimental design should be built to actually test it (not just assume it).

We're also keeping a fallback architecture in mind: instead of the LLM directly replacing the autoscaler, it could act as a **high-level planner** (interpreting context, adjusting targets/constraints) sitting on top of a fast, stable **low-level controller** (HPA or PID) that does the actual second-to-second scaling. If a direct LLM-as-controller setup turns out to be too slow, unstable, or unreliable, this is the architecture we pivot to.

## Who is our advisor?

Our supervisor is **Dr. Farhana Zulkernine** (Queen's University), whose work spans cloud computing, AI, and decision support systems, and who leads the Big-data Analytics and Management (BAM) Lab.

She raised an important, still-unresolved methodological concern early on: *"The problem with resource management projects is that you must have low-level access to resources to show that your system can work at scale."* We don't currently have dedicated cloud infrastructure to validate at production scale — local Kubernetes development is useful for building and iterating, but it cannot by itself prove the system works under real production load. Treat this as an open research-design problem, not a solved one — if you're designing an experiment, ask whether its conclusions would actually survive a reviewer asking "but does this work outside your laptop?"

## What's the plan?

**Phase 1 — Local Kubernetes prototype (current):**
Containerize a simple service, deploy it to a local cluster (Minikube or kind), generate traffic with Locust, and get Kubernetes HPA automatically scaling it from 1 to N pods. This is our onboarding milestone — once it works, the skeleton of the whole experiment exists.

**Phase 2 — Controlled experimental testbed:**
Replace HPA with our own controller(s). Build out the LLM-based controller (metric-only → metric+history → metric+context → hierarchical variants), and run controlled comparisons: same environment, same workload trace, different controller.

**Phase 3 — Limited real-cloud validation (if resources/budget permit):**
Some validation beyond local Kubernetes — this is the part Dr. Zulkernine flagged as unresolved. We should scope this honestly rather than assume it'll happen.

## What will I learn?

Depending on which part of the project you work on:

- Docker and containerization
- Kubernetes (pods, deployments, services, HPA, metrics)
- Workload generation and benchmarking (Locust)
- LLM agent design: state representation, prompt design, structured outputs, memory, guardrails
- Experimental methodology: fair baselines, controlled variables, statistical rigor, reporting negative results
- Working with an AI coding agent (Claude Code) as part of the actual dev workflow

## Related work

Before writing any code, skim **[related_works.md](related_works.md)** — a short, deliberately non-overwhelming list of papers that actually matter to this project and why.

## How do I navigate the repo?

See **[repo.md](repo.md)**.
