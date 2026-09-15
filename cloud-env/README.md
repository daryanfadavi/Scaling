# cloud-env/

The Phase 1 test environment: the app we scale, and the infrastructure/workload tooling around it.

Planned structure as this fills in:

```
cloud-env/
├── app/           # the synthetic FastAPI service + Dockerfile
├── k8s/           # Deployment, Service, HPA manifests
└── workloads/     # Locust workload definitions (ramp, spike, bursty, periodic, ...)
```

See [docs/onboarding/setup.md](../docs/onboarding/setup.md) for how this gets built and run.
