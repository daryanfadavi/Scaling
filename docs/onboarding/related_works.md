# Related work

A short list, on purpose — read these before writing code, and understand *why* each one matters to SCALE rather than just skimming the abstract.

## DeepRM

**Mao, Alizadeh, Menache, Kandula (2016), "Resource Management with Deep Reinforcement Learning."**

Instead of hand-designing scheduling heuristics, DeepRM trains an RL agent to learn a resource-management policy from experience. One notable finding: under heavy load, the learned policy sometimes deliberately left resources idle so future short jobs could be served quickly — a strategy nobody explicitly programmed in.

**Why it matters here:** DeepRM is our conceptual reference point for "learned resource management," even though it isn't an autoscaling paper specifically. Our framing borrows its structure directly:

```
DeepRM:   system state  → RL policy  → resource decision
SCALE:    metrics+context → LLM agent → autoscaling decision
```

When someone asks "why not just use RL," this is the paper to point to — and the honest answer is that RL can learn very strong policies, but needs environment interaction and reward engineering, and doesn't natively take qualitative context as input the way an LLM prompt can.

## Kubernetes Horizontal Pod Autoscaler (documentation, not a paper)

[kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale](https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/)

**Why it matters here:** this is our primary baseline. Understand how it actually computes desired replicas, its stabilization windows, tolerances, and scale-up/down policies — you need to know these mechanics well enough to explain *why* HPA does or doesn't handle a given workload well, not just that it exists.

## ReAct: Synergizing Reasoning and Acting in Language Models

Yao et al. (2022).

**Why it matters here:** relevant background for how an LLM can be structured to interleave reasoning with taking discrete actions — useful when designing the controller's prompt/action loop (Phase 2).

## El Mezouar et al., "Large Language Model Integration with Reinforcement Learning to Augment Decision-Making in Autonomous Cyber Operations"

**Why it matters here:** one of the few existing examples of LLM-augmented decision-making in a dynamic, adversarial operational environment — structurally similar to what we're attempting with cloud autoscaling (an LLM reasoning over changing conditions to inform/augment lower-level automated decisions).

## DeathStarBench (background only, not required yet)

A realistic microservice benchmark suite. We are *not* starting with this — Phase 1 uses a single synthetic service on purpose, to keep the focus on the autoscaling question rather than application complexity. Worth knowing it exists in case Phase 2/3 experiments call for a more realistic multi-service setup.

---

If you find a paper that changes how we should think about the research question or the experimental design, add it here with a one-paragraph "why it matters" — don't just drop a citation with no context.
