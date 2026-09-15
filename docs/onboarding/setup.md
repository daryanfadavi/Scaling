# Dev environment setup

This gets you to the Phase 1 milestone: a containerized app running on a local Kubernetes cluster, scaled automatically by HPA under Locust-generated traffic. You do **not** need an AWS/Azure account for any of this — everything runs locally.

## 1. Install the tooling

| Tool | What it's for | Install |
|---|---|---|
| Docker | Containerize the app | [docs.docker.com/get-docker](https://docs.docker.com/get-docker/) |
| kubectl | Talk to the Kubernetes cluster | [kubernetes.io/docs/tasks/tools](https://kubernetes.io/docs/tasks/tools/) |
| Minikube *or* kind | Run Kubernetes locally | [minikube.sigs.k8s.io](https://minikube.sigs.k8s.io/docs/start/) or [kind.sigs.k8s.io](https://kind.sigs.k8s.io/docs/user/quick-start/) |
| Python 3.11+ | The app and workload scripts | [python.org/downloads](https://www.python.org/downloads/) |
| Locust | Workload generation | `pip install locust` |

If you're unsure whether to pick Minikube or kind: either is fine for Phase 1. Minikube has a friendlier UI/dashboard; kind starts faster and is closer to what CI systems usually use. Pick one and be consistent across the team so results are comparable.

## 2. Clone the repo

```bash
git clone https://github.com/<org>/SCALE.git
cd SCALE
```

## 3. Start your local cluster

```bash
# Minikube
minikube start

# or kind
kind create cluster --name scale
```

Verify it's up:

```bash
kubectl get nodes
```

## 4. Enable the metrics server

HPA needs the metrics server to read CPU/memory usage.

```bash
# Minikube
minikube addons enable metrics-server

# kind
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
```

## 5. Build and deploy the test app

Instructions live in `cloud-env/README.md` once the initial FastAPI service and manifests are committed (Phase 1 work-in-progress). At a high level, the flow is:

```bash
docker build -t scale-app:local ./cloud-env/app
kubectl apply -f cloud-env/k8s/
kubectl get pods -w
```

## 6. Generate load and watch it scale

```bash
locust -f cloud-env/workloads/ramp.py --host http://<service-ip>:<port>
```

Then in another terminal:

```bash
kubectl get hpa -w
```

You should see replica count increase as load ramps up, and decrease again once it drops — that's the Phase 1 milestone.

## Troubleshooting

- **Pods stuck in `Pending`:** usually a resource request issue — check `kubectl describe pod <name>` for scheduling errors.
- **HPA shows `<unknown>` for targets:** the metrics server likely isn't ready yet; give it a minute after enabling, then check `kubectl top pods`.
- **Can't reach the service:** for Minikube, use `minikube service <name> --url` to get a reachable URL; for kind, you may need port-forwarding: `kubectl port-forward svc/<name> 8080:80`.

If you hit something not covered here, add it to this file once you've solved it — that's the whole point of an onboarding doc.
