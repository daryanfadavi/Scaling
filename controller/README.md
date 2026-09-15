# controller/

Decision-making logic for autoscaling — both the baseline(s) and the LLM-based controller(s).

Expect subfolders per controller variant as the design solidifies, e.g.:

```
controller/
├── hpa_baseline/        # wraps/mirrors Kubernetes HPA behavior for fair comparison
├── llm_metric_only/     # LLM controller with only telemetry as input
├── llm_with_history/    # + recent state/action history
├── llm_contextual/      # + natural-language operator context
└── hierarchical/        # LLM planner + fast low-level controller
```

Controllers must not know they are being evaluated — see [AGENTS.md](../AGENTS.md) and [CONTRIBUTING.md](../CONTRIBUTING.md) for why that separation matters.
