# Camp QMIND build (Oct 3–4): racing autoscalers in a simulator

Our goal is to race three autoscaling controllers on **identical** simulated traffic and see who keeps latency low without wasting money:

1. **HPA baseline**: Kubernetes' replica formula, the thing we're trying to beat.
2. **LLM controller**: sees telemetry (CPU, latency, request rate, …) and answers with a JSON action.
3. **LLM + context controller**: the same, plus natural-language operator notes like *"Marketing campaign launches in ~90 seconds; expect ~5x traffic."*

The research question in miniature: **does the context actually help, and when?** HPA can't read notes. If the LLM + context controller scales up *before* the spike, it should beat HPA on the `spike_with_warning` scenario. If it doesn't, that's a result too.

The simulator, runner, plots, tests and API client are already built. Your job is the controllers.

## Why the startup delay matters

New replicas take **2 ticks (30 simulated seconds)** to start before they serve traffic. Removing replicas is instant.

That delay is the whole point. If new replicas were instant, HPA could wait for the spike and react with no penalty, and knowing about the spike in advance would be worth nothing. With the delay, a purely reactive controller always gets a few overloaded ticks after a sudden spike. A controller that scales up *before* the spike can avoid them. That gap is what we're measuring. (Details are in the long comment at the top of `simulator/env.py`.)

## How the simulator works

| Thing | Value |
|---|---|
| Tick | 15 simulated seconds; every scenario is 80 ticks (20 min) |
| Capacity | each *ready* replica serves 50 req/s at 100% CPU |
| CPU | `100 × load / (ready × 50)`, capped at 100 |
| Overload | requests that can't be served queue up as a **backlog** for the next tick |
| Latency | 50 ms base, grows like `50 / (1 − utilization)` near 100%, plus backlog wait; capped at 2000 ms |
| SLA | a tick **violates the SLA** if latency > 300 ms |
| Replicas | start at 2; always between 1 and 20 |
| Guardrails | the environment clamps every controller's target to [1, 20] and to ±4 per tick (logged in the CSV `clamp_note` column) |
| Noise | load ±5%, latency ±3%, from a seed. **Same seed means the same trace for every controller.** |

Scenarios live in `experiments/configs/`:

- `ramp`: 100 → 500 req/s, rising steadily over ticks 10–50. HPA should handle this fine.
- `spike`: 100 req/s, jumps to 500 at tick 30 for 20 ticks. No warning.
- `spike_with_warning`: the **identical** trace, plus an operator note at tick 24 (90 s early).

A controller only ever sees an `Observation` (see `controller/base.py`). It never sees the scenario name, the config, or the future. Keep it that way: if a controller could "know" it's in the spike scenario, the comparison would be meaningless.

## Setup

You need Python 3.11+.

```bash
git checkout setup/camp-sim          # (or main, once this is merged)
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                 # then paste your API key after ANTHROPIC_API_KEY=
```

**Never commit `.env`.** It's gitignored; keep it that way.

Check everything works:

```bash
pytest                               # all green (the todo specs are skipped by default)
python -m experiments.run --controller static --scenario spike    # the smoke test
python scripts/check_api.py          # one real API call (Pair B needs this; it's fine to fail without a key)
```

The smoke test prints a summary and writes a CSV, a JSON summary and a PNG to `experiments/results/spike/static2/`. Open the PNG. Two replicas can't even handle the base load, so it violates the SLA on every tick. That's what "never scale" looks like.

## Warm-up game: you are the autoscaler

```bash
python -m experiments.run --controller human --scenario spike_with_warning --label yourname
```

Each tick, type `+N`, `-N`, or press Enter. Read the operator notes. Your score goes on the same leaderboard as the controllers. Pay attention to what *you* did when the note appeared, because that's what we want the LLM to do.

## The pair tasks

Each pair's task has a spec in `tests/todo/`. **Your goal is to make it green:**

```bash
pytest -m todo                       # all the todo specs
pytest -m todo tests/todo/test_llm_parser.py
```

### Pair A: HPA baseline (`controller/hpa_baseline.py`)

Implement `HPAController.decide` (and `reset`). The docstring has the formula, the tolerance band, the stabilization window and worked examples.

**Done when:**
- [x] `pytest tests/core/test_hpa.py` passes (done; moved from `tests/todo/` so it always runs)
- [ ] `python -m experiments.run --controller hpa --scenario <s>` runs for all three scenarios
- [ ] You can explain, from the plot, why HPA has SLA violations on `spike` but not (much) on `ramp`

### Pair B: LLM controller (`controller/llm_controller.py`)

Write `SYSTEM_PROMPT`, `build_prompt(obs)` and `parse_response(text)`. The API call, caching, fallback, and the metric-only / with-context variants are already wired up. The docstrings have the output schema and hints.

**Done when:**
- [ ] `pytest -m todo tests/todo/test_llm_parser.py` passes
- [ ] `python scripts/check_api.py` succeeds
- [ ] `python -m experiments.run --controller llm --scenario spike_with_warning` and `--controller llm_context ...` both run with few or no invalid decisions
- [ ] You've read the `reason` column in the CSV: does the context variant mention the campaign?

Don't change `visible_observation()` and don't leak anything into the prompt that isn't in `obs`. Those two rules are what make the comparison fair.

## Running the race

```bash
# each controller x each scenario (LLM runs take ~1–2 min each: one API call per tick)
for s in ramp spike spike_with_warning; do
  python -m experiments.run --controller hpa --scenario $s
  python -m experiments.run --controller llm --scenario $s
  python -m experiments.run --controller llm_context --scenario $s
done

python -m experiments.plot           # time-series per scenario + leaderboard
```

Outputs (all in the gitignored `experiments/results/`):
- `plots/<scenario>_timeseries.png`: all controllers overlaid (load vs capacity, replicas, latency with the SLA line)
- `plots/leaderboard.png` and `leaderboard.md`: SLA violations and replica-minutes per controller (mean ± std when you have repeats)

Useful flags:
- `--seed N`: a different (but still reproducible) traffic trace
- `--repeats N`: run an LLM controller N times on the same trace to see how consistent it is (each repeat is a fresh API call)
- `--offline`: replay cached LLM responses only, with no API calls (great for re-plotting or demos)
- `--label X`: a custom name on the leaderboard (e.g. `llm_context_v2` after a prompt change)
- `--no-history`: the LLM variant without the last-5-ticks history
- `-v`: log every guardrail clamp

Every LLM response is cached in `experiments/cache/` (gitignored), keyed on model + prompt + repeat number.

## Stretch goals

- **Repeats and seeds:** run `--repeats 3` for the LLM variants and `--seed 0/1/2` for everything. Is the LLM's advantage bigger than its run-to-run variance?
- **Scheduled baseline** (`controller/scheduled_baseline.py`): a cron-style controller that pre-scales at a time the operator gives it. If it does as well as the LLM + context controller, then the LLM isn't what's helping; the *information* is. That's an important control.
- A controller-level cooldown in `LLMController.apply_guardrails`.
- New workload shapes in `simulator/workloads.py` (bursty, periodic) or a misleading operator note (a campaign that never comes).

## Scope: this is a pilot, not proof

The simulator is deliberately simple: linear capacity, a textbook queueing curve, a fixed 30 s startup, and no LLM latency in the loop (the decision is applied the same tick regardless of how long the API took). Results here tell us **which experiments are worth running on real Kubernetes**. They are not evidence that LLM autoscaling works in production.
