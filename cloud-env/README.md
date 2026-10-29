# cloud-env/

The Phase 1 test environment: the app we scale, and the infrastructure/workload tooling around it.

**Not built yet: writing this folder is the team's Phase 1 exercise.** The requirements and the traps to avoid are in [setup.md](../docs/onboarding/setup.md#your-task-build-cloud-env).

Planned structure:

```
cloud-env/
├── app/           # the synthetic FastAPI service + Dockerfile
├── k8s/           # Deployment, Service, HPA manifests
└── workloads/     # Locust workload definitions (ramp, spike, bursty, periodic, ...)
```

See [docs/onboarding/setup.md](../docs/onboarding/setup.md) for how this gets built and run.
