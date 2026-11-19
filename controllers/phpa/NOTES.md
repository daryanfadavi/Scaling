# PHPA — Predictive Horizontal Pod Autoscaler

**Repo:** https://github.com/jthomperoo/predictive-horizontal-pod-autoscaler
**Version used:** v0.13.2 (July 2023) · API group `jamiethompson.me/v1alpha1` · requires k8s ≥ 1.23
**Docs:** https://predictive-horizontal-pod-autoscaler.readthedocs.io

## What it is

An open-source Kubernetes **operator** (a program that runs inside the cluster and
watches for changes to a custom resource) that wraps the standard Horizontal Pod
Autoscaler and adds **forecasting** on top of it.

Standard HPA is purely reactive: every sync period it reads a metric, compares it to
a target, and computes

```
desiredReplicas = ceil( currentReplicas × currentMetric / targetMetric )
```

By the time CPU is high, the load is already there and the scale-up is late.

PHPA keeps that calculation but adds a memory of past decisions and projects it forward.

## How it works

Every sync period (default **15s**):

1. **Run the normal HPA calculation** — identical formula, identical metrics sources
   (metrics-server, or the custom/external metrics APIs).
2. **Append the result to a time series** of replica counts, stored in a ConfigMap
   named `predictive-horizontal-pod-autoscaler-<name>-data`.
3. **Fit a model** to that series:
   - **Linear regression** — least-squares line through the last `historySize` points,
     extrapolated `lookAhead` ms forward. Catches ramps and smooths sudden drops.
   - **Holt-Winters** — triple exponential smoothing. Tracks three components
     separately: **level** (current value), **trend** (direction of change), and
     **season** (repeating cycle shape). Learns a recurring pattern and pre-scales
     *before* a known peak.
4. **Decide** via `decisionType`, default `maximum` — take whichever is higher, the
   HPA value or the forecast. The forecast can therefore only *add* capacity;
   HPA remains a safety floor. (Other options: `minimum`, `mean`, `median`.)
5. **Apply `behavior`** — stabilization windows and rate limits, same schema as HPA v2 —
   then clamp to `minReplicas`/`maxReplicas` and patch the Deployment's scale subresource.

## Holt-Winters parameters

| Field | Meaning |
|---|---|
| `alpha` | smoothing for **level** (0–1) |
| `beta` | smoothing for **trend** (0–1) |
| `gamma` | smoothing for **seasonality** (0–1) |
| `seasonalPeriods` | length of one season, counted in sync periods |
| `storedSeasons` | how many seasons of history to retain |
| `trend` / `seasonal` | `additive` or `multiplicative` |

**Cold start:** Holt-Winters refuses to predict until it has at least
`2 × seasonalPeriods` data points. Until then PHPA behaves exactly like plain HPA.

## Architectural notes

- PHPA's CRD is near-identical to an HPA spec plus a `models:` block, which makes it a
  clean A/B comparison against our existing Camp QMIND HPA.
- **Never attach both an HPA and a PHPA to the same Deployment** — they will fight over
  the scale subresource.
- It forecasts *the HPA's own past recommendations*, not raw traffic. Cheap and
  metric-agnostic, but it inherits HPA's lag, and history is censored if HPA was ever
  pinned at `maxReplicas` during a peak.
- `runtimeTuningFetchHook` lets an external HTTP service receive the replica history and
  return `{alpha, beta, gamma}` per calculation — a possible integration point for an LLM
  in a slow tuning loop.

## Why it's in our comparison set

HPA, KEDA, KPA, VPA and scheduled scaling are all reactive or static — none of them
predict. PHPA is the strongest *proactive without an LLM* baseline that exists as
installable software. If our LLM controller can't beat three-parameter exponential
smoothing on repeating load, that needs to surface now rather than at demo.

The expected result is that it won't, on periodic traffic. Holt-Winters is already
near-optimal there, and it's faster, cheaper and deterministic. Our differentiator has to
be **context that doesn't exist in the metrics** — one-off announced events that no
statistical model can anticipate — plus explainable decisions.

## Known limitations

- Unmaintained: last release July 2023, `v1alpha1` API. Install compatibility is the
  first thing to verify.
- Mis-tuned Holt-Winters causes replica thrashing and latency spikes (documented by the
  author in his own 3-day experiment).
- Blind to one-off events; only learns what repeats.
- `decisionType: maximum` biases toward over-provisioning.
- ConfigMap history has a 1 MiB ceiling — long seasons at short sync periods will grow it.