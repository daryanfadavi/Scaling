# cloud-env/

Phase 1 test environment: a CPU-burning FastAPI app on a local Kubernetes cluster, autoscaled by HPA under Locust traffic.

```
cloud-env/
├── app/         # FastAPI service (/healthz, /work burns ~5 ms CPU) + Dockerfile
├── k8s/         # Deployment, Service, HPA (autoscaling/v2, 60% CPU, 1–10 replicas)
└── workloads/   # ramp.py (Locust LoadTestShape) + locust-pod.yaml
```

## Run it (Minikube)

Prereqs: Docker running, minikube, kubectl. See [setup.md](../docs/onboarding/setup.md).

```bash
minikube start
minikube addons enable metrics-server
kubectl top nodes                      # wait until this prints numbers

# Build the app and load it into the cluster
docker build -t scale-app:local ./cloud-env/app
minikube image load scale-app:local

# Deploy app, service and HPA
kubectl apply -f cloud-env/k8s/
kubectl get hpa                        # TARGETS shows cpu: X%/60% after ~1 min

# Run Locust inside the cluster
kubectl create configmap locustfile --from-file=cloud-env/workloads/ramp.py
kubectl apply -f cloud-env/workloads/locust-pod.yaml
kubectl logs -f locust                 # request stats

# In another terminal
kubectl get hpa -w
```

## Load shape (`ramp.py`)

| Time    | Users |
|---------|-------|
| 0–1 min | 10    |
| 1–2 min | 30    |
| 2–3 min | 60    |
| 3–7 min | 60 (hold) |
| 7–8 min | 10    |
| 8 min   | stop  |

## Expected result

Example run: CPU rose to 200%/60% and replicas scaled 1 → 2 → 4, settling around 52% CPU on 4 pods. After the load stopped, replicas stayed at 4 for the 5-minute stabilization window, then scaled back down.

## Rerun / cleanup

```bash
kubectl delete pod locust
kubectl delete configmap locustfile
# then repeat the Locust steps above

minikube stop                          # pause the cluster, keeps everything
```

## Notes

- `requests.cpu: 200m` is required: HPA computes CPU % relative to it.
- Image tag `:local` + `imagePullPolicy: IfNotPresent` uses the loaded image instead of pulling.
- Locust runs in-cluster against `http://scale-app` so load spreads across all pods (port-forward would hit only one).
- If minikube start shows "connection refused" errors on add-ons, run `minikube delete --all --purge` and start again.