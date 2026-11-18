# phpa: Predictive Horizontal Pod Autoscaler

A manifests-plus-scripts plugin. `apply.sh` installs the PHPA operator with Helm
(chart v0.13.2, release `phpa-operator`, namespace `default`), the runner applies
`manifests/phpa.yaml`, and `remove.sh` deletes the PHPA object between runs.

PHPA runs the standard HPA calculation every sync period, records each result as a
time series of replica counts, fits a model to that series, and takes whichever is
higher: the HPA value or the model's forecast. The forecast can only add capacity;
HPA stays as a floor.

| Setting | Value |
|---|---|
| Metric | CPU, average utilization relative to the pod's CPU request (100m) |
| Target | 60% |
| Replicas | 1 to 10 |
| Model | Linear regression (`historySize: 6`, `lookAhead: 30000` ms) |
| Decision | `maximum`: higher of the HPA value and the forecast |
| Sync period | 15 s (`syncPeriod: 15000`) |
| Scale up | Same as `hpa/`: no stabilization window; at most +100% or +4 pods per 15 s |
| Scale down | Same as `hpa/`: 300 s stabilization window; at most -100% per 15 s |
| Built in | 10% tolerance, inherited from the HPA calculation PHPA reuses |

**Everything except the model block is copied from `controllers/hpa/manifests/hpa.yaml`.**
Same metric, same 60% target, same bounds, same `behavior`. The only difference between
the two controllers is the forecasting layer, so any difference in results comes from
prediction rather than from a different operating point.

**Why linear regression and not Holt-Winters.** Linear fits a least-squares line through
the last `historySize` replica recommendations and extrapolates `lookAhead` forward, so it
produces forecasts after 6 sync periods (90 s). Holt-Winters needs `2 × seasonalPeriods`
of history before it predicts anything, which on the current scenarios is most of the run:
it would behave identically to plain HPA and the comparison would show nothing. Holt-Winters
is the more interesting model and needs a scenario with several repeating cycles to be
worth running.

**`lookAhead: 30000`** is two sync periods, chosen as a rough estimate of how long a new pod
takes to go from scheduled to serving traffic. It should be replaced with a measured value
from the gap between `replicas_desired` and `replicas_ready` in a run's `metrics.csv`.

**Observation note (controllers/README.md, rule 3):** PHPA reads CPU from metrics-server,
the same source as `hpa/`, not from Prometheus or `telemetry/`, because it runs inside the
cluster as an operator.

**Why `remove.sh` leaves the operator installed.** The runner applies `manifests/` *before*
running `apply.sh`, so the CRD must already exist when `kubectl apply` runs. `helm uninstall`
of this chart removes the CRD with it, which breaks the next run. So `remove.sh` deletes only
the `PredictiveHorizontalPodAutoscaler` object, guarded by a check that the CRD exists at all
so it also succeeds on a cluster that has never had PHPA installed. The operator stays
resident between runs, like KEDA does. `apply.sh` uses `helm upgrade --install`, so
reinstalling is a no-op.

The tradeoff: an idle PHPA operator consumes a small amount of CPU during `hpa` and `keda`
runs. Removing it instead would destroy the CRD every time and break the runner's ordering.

Quick check: `python experiments/run_experiment.py --controller phpa --scenario gradual_ramp --duration 180`.