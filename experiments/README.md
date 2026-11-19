# experiments/

Two separate things live here:

- **Cluster experiments** (current work): real controllers on the local Kubernetes cluster
  under Locust traffic. `run_experiment.py`, `scenarios/`.
- **Camp QMIND simulator** (earlier onboarding exercise, unchanged): `run.py`, `plot.py`, `configs/`.
  See [camp_qmind.md](../docs/onboarding/camp_qmind.md).

```
experiments/
├── run_experiment.py   # cluster runner (below)
├── summarize_cluster.py # scores and compares cluster runs (below)
├── prompt_lab.py       # replays recorded situations against prompt variants, no cluster (below)
├── prompt_lab_cases/   # recorded situations for the prompt lab (small JSON, committed)
├── scenarios/          # Locust scenarios for the cluster runner
├── run.py, plot.py     # simulator runner and plots
├── configs/            # simulator scenario YAMLs
├── results/            # outputs of both (gitignored)
└── cache/              # simulator LLM response cache (gitignored)
```

## Running a cluster experiment

Set up the cluster first ([docs/onboarding/setup.md](../docs/onboarding/setup.md)), then:

```bash
python experiments/run_experiment.py --controller <name> --scenario <name> \
    [--repeat N] [--duration S] [--initial-replicas N]
```

| Option | Default | Meaning |
|---|---|---|
| `--controller` | required | Folder under [`controllers/`](../controllers/) (`hpa`, `keda`, `phpa`, `llm`) |
| `--scenario` | required | File under [`scenarios/`](scenarios/), without `.py` (e.g. `gradual_ramp`) |
| `--repeat` | 1 | Run repeats 1..N. Existing repeat folders are skipped, never overwritten. |
| `--only-repeat` | | Run only repeat N (used by `run_batch.py`) |
| `--duration` | 600 | Load test length in seconds |
| `--initial-replicas` | 1 | Pods at the start of each run |

Example: `python experiments/run_experiment.py --controller hpa --scenario gradual_ramp --repeat 3`

### What every run does, in order

1. **Reset:** stop and remove every controller (every plugin's remove steps, plus any HPA or KEDA
   ScaledObject left in namespace `scale`), scale `scale-app` to `--initial-replicas`, and wait until
   exactly that many pods are ready and none are starting or terminating.
2. **Apply** the controller's plugin (`apply.sh`, `manifests/`, then `start.sh` in the background).
3. **Settle:** wait until all pods are ready, then 60 s more. If the controller scaled the idle app
   away from `--initial-replicas` in that time, it's removed and steps 1–3 start over (up to 5
   attempts, recorded as `setup_attempts` in `metadata.json`), so every controller starts the load
   test at the same pod count.
4. **Load test:** Locust runs the scenario headless for `--duration` seconds against the app's
   NodePort (through `minikube service`, which spreads load across all pods).
5. **Export:** after 30 s (for the last Prometheus scrape), one telemetry snapshot per 15 s for the
   load test window → `metrics.csv`.
6. **Remove** the controller. This runs even if an earlier step failed.
7. **Metadata** → `metadata.json`.

The fixed settings (settle time, replica bounds, kube context) are constants at the top of
`run_experiment.py`. If a run fails, its partial folder is renamed `repeat_N_failed_<time>` so a rerun
redoes it.

### Running the whole showcase batch

`python experiments/run_batch.py --dry-run` prints the plan (every controller × `daily_wave`,
`random_bursts`, `busy_day`, two repeats, interleaved) and the estimated finish time; drop
`--dry-run` to run it, `--resume` to continue after a stop. See the docstring at the top of
`run_batch.py` for the LLM health check (runs where most model calls failed are set aside as BAD).

### Output

`experiments/results/<scenario>/<controller>/repeat_<N>/`:

| File | Contents |
|---|---|
| `metrics.csv` | One row per 15 s: replicas, CPU, request rate, latency p50/p95, error rate. Format: [docs/telemetry.md](../docs/telemetry.md) |
| `metadata.json` | Controller, scenario, repeat, start/end times, duration, initial replicas, git commit (and whether the tree had uncommitted changes), machine, cluster config (Kubernetes version, Minikube CPUs/memory, Helm chart versions, app image and resources), file hashes of the scenario and plugin, and the exact telemetry queries |
| `locust_stats.csv`, `locust_failures.csv`, `locust_stats_history.csv`, ... | Locust's own client-side numbers |
| `locust.log` | Locust output |
| `controller.log` | stdout/stderr of a process-based controller (e.g. llm) |

Results are gitignored. Share them some other way (or commit a summary), and always keep `metadata.json` with the CSV.

## Comparing runs

```bash
python experiments/summarize_cluster.py [--scenario S ...] [--controller A B ...]
```

Scores every run folder and writes `experiments/results/cluster_runs.csv` (one row per run) and
`cluster_summary.md` (one row per scenario and controller, mean ± standard deviation over repeats).
Per run: SLA-violation ticks (p95 above 300 ms), pod-minutes (the cost), peak pods, scale events, flaps
(direction reversals), peak p95, and for LLM controllers the number of failed or invalid model calls.

**A run with failed model calls gets a warning.** The controller treats a failed call as "do nothing", so
a broken model call looks like a calm controller. Don't read such a run as the model choosing to hold.

## Prompt experiments

The LLM controller's prompt has named variants in [`controllers/llm/prompt.py`](../controllers/llm/prompt.py),
each differing from the baseline `cluster-v1` by one thing. Each has a plugin folder, so its runs land in their own results folder:

| Controller (`--controller`) | Prompt | Change from the baseline |
|---|---|---|
| `llm` | `cluster-v1` | none (the baseline) |
| `llm_nohist` | `cluster-v1-nohist` | the model sees no past decisions |
| `llm_trend` | `cluster-v2-trend` | adds a trend line (rising / falling / flat) computed from the controller's own recent readings |
| `llm_words` | `cluster-v2-words` | adds a word (low / high / ...) next to CPU, latency and errors |

Change one thing per variant, give it a new name, and add it to `VARIANTS`. Nothing in a prompt may mention the experiment.

**Cheap first pass, no cluster:** [`prompt_lab.py`](prompt_lab.py) turns the ticks of a finished run (`controller.log`) into
cases and asks each variant what it would do, several times each. It is open-loop (a frozen moment, not its consequences), so use it
to shortlist prompts and confirm the shortlist on the cluster with repeats.

```bash
python experiments/prompt_lab.py extract experiments/results/<scenario>/llm/repeat_1/controller.log --name <name> --out experiments/prompt_lab_cases/<name>.json
python experiments/prompt_lab.py run --cases experiments/prompt_lab_cases/<name>.json --ticks 9 15 --repeats 5   # live API calls
```

Cases can carry an `expect` list (the actions that count as sensible) with an `expect_note`. These are human judgements; say so in the note.

## Controller plugins

See [controllers/README.md](../controllers/README.md) for the plugin contract: `manifests/`, `apply.sh`,
`remove.sh` and `start.sh`/`stop.sh` for controllers that run as a separate process, the environment
they get, and the fairness rules.

## Adding a scenario

See [scenarios/README.md](scenarios/README.md).

## Before adding a new experiment

Open an issue describing the question it answers and what would count as a meaningful result either
way. See [CONTRIBUTING.md](../CONTRIBUTING.md).
