# Runbook: bring up the cluster and test the LLM controller on Windows (WSL2)

**Audience: Claude Code (or any agent) working on Harsh's Windows PC.** A human can follow it too.
Goal: get the cluster running and do a short test run of `controllers/llm/`. Do **not** collect
final numbers: Daryan collects those on one machine so results are comparable
(see [setup.md](setup.md) and [controllers/README.md](../../controllers/README.md)).

## Background reading

[docs/phase1_run.html](../phase1_run.html) is the team's "SCALE LLM Track Update" page (open it in a browser):
Phase 1 HPA results, research on how an LLM could run autoscaling, and the plan for the Nov 21 showcase.
It is **history, not the current setup**: it describes the earlier kind cluster, a 1 to 8 replica range and an
HPA-only run. The current setup (Minikube, 1 to 10 replicas, shared runner and telemetry) is in
[setup.md](setup.md) and [controllers/README.md](../../controllers/README.md).

## Ground rules

- **Work inside WSL2 (Ubuntu), in the Linux home folder** (`~/Scaling`), never under `/mnt/c`.
  The runner uses POSIX process groups (`os.killpg`), so it doesn't work on native Windows.
- **Don't install software yourself.** If a tool is missing, give Harsh the exact command and wait.
- **Never print, log or commit the API key.** It lives in `.env` (gitignored). Don't `cat` that file.
- **Don't change the cluster settings** at the top of `scripts/setup_cluster.sh`, the app, the
  scenarios or other controllers. Don't merge to `main`; work on `feature/llm-cluster-controller`.
- Anything that looks wrong is a finding to report, not something to paper over.

## 1. Check the machine (stop and report if any check fails)

```bash
uname -a                       # should mention "microsoft" / WSL2
docker info | grep -E "Server Version|CPUs|Total Memory"
df -h ~ | tail -1              # want 20 GB or more free
for t in git python3 minikube kubectl helm; do command -v $t >/dev/null && echo "ok $t" || echo "MISSING $t"; done
```

- `docker info` must print a Server section, **CPUs >= 6 and Total Memory >= 7 GiB**. If lower, tell
  Harsh to set `memory=8GB` and `processors=6` in `C:\Users\<name>\.wslconfig`, run `wsl --shutdown`
  in PowerShell, and restart Docker Desktop (with the WSL2 backend there are no sliders in Docker Desktop).
- If Docker isn't reachable: Docker Desktop must be running, with Settings -> Resources -> WSL integration
  enabled for this Ubuntu.
- The full list of installs is in Harsh's chat; the short version is Docker Desktop, then
  `minikube`, `kubectl`, `helm` and `python3-venv` inside Ubuntu.

## 2. Get the code on the right branch

```bash
cd ~
git clone https://github.com/daryanfadavi/Scaling.git     # skip if ~/Scaling exists
cd Scaling && git fetch --all
git checkout feature/llm-cluster-controller
```

If that branch isn't on the remote yet, Harsh copies `controllers/llm/`, `tests/core/test_cluster_llm.py`
and the edit to `controllers/README.md` onto `main` instead. Confirm with:

```bash
ls controllers/llm            # README.md main.py prompt.py start.sh
```

Line endings: if a script fails with `\r: command not found`, run
`git config core.autocrlf input`, then re-checkout the files (`git checkout -- .`).

## 3. Python environment and tests

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install -U brotli      # old Brotli breaks the Anthropic SDK: replies fail with "connection problem"
pytest -q tests/core/test_cluster_llm.py tests/core/test_controllers.py
```

All of these should pass. No cluster or key is needed for them.

## 4. API key

Harsh creates `.env` in the repo root with one line, `ANTHROPIC_API_KEY=<key>`. Check it's present
and ignored without showing it:

```bash
test -s .env && echo ".env present"; git check-ignore -q .env && echo ".env ignored"
```

Optional live check (one cheap call with a made-up snapshot):

```bash
python - <<'EOF'
from dotenv import load_dotenv; load_dotenv(".env")
from controller.llm_client import LLMClient
from controller.llm_controller import parse_response
from controllers.llm.prompt import SYSTEM_PROMPT, build_prompt
s = dict(replicas_ready=2.0, replicas_starting=0.0, cpu_utilization_pct=310.0, request_rate_rps=24.0,
         latency_p50_ms=180.0, latency_p95_ms=620.0, error_rate=0.02)
r = LLMClient().complete(SYSTEM_PROMPT, build_prompt(s, 2, 1, 10, []), use_cache=False)
print("CALL FAILED" if r is None else parse_response(r.text))
EOF
```

Expect `SCALE_UP` with `invalid=False`. `CALL FAILED` means a key, network or Brotli problem.

## 5. Bring up the cluster

```bash
./scripts/setup_cluster.sh 2>&1 | tee setup.log
```

About 10 minutes the first time. It ends with `Cluster ready.` and a summary. It is safe to re-run.
If it fails, read the last lines of `setup.log` and match them to the troubleshooting list in
[setup.md](setup.md); report the failing step. Don't edit the script to get past it.

## 6. Test run of the LLM controller

```bash
python experiments/run_experiment.py --controller llm --scenario gradual_ramp --duration 180
```

About 5 minutes, plus settle time. It makes roughly 20 to 30 live API calls. Then check:

```bash
D=experiments/results/gradual_ramp/llm/repeat_1
head -1 $D/controller.log                     # model, prompt_version, cooldowns, bounds
grep -c '"applied": true' $D/controller.log   # how many times it scaled
grep -c '"invalid": true' $D/controller.log   # should be 0 or close to it
grep -o '"error": "[^"]*"' $D/controller.log | sort | uniq -c   # observe or scale failures
cut -d, -f3,5,6,7,8,11 $D/metrics.csv | head -30   # elapsed_s, 3 replica columns, cpu, p95 (docs/telemetry.md)
```

What good looks like: `replicas_desired` rises as the load ramps and falls afterwards, no `error`
lines, and `llm_latency_s` of a few seconds or less.

## 7. Report back (to Harsh, in chat)

- Result of each step above, including any command that failed and its last lines of output.
- From the run: total decisions, number applied, number invalid, min and max `replicas_desired`,
  and the peak `latency_p95_ms`. State plainly that this is one short test run, not a result.
- Anything odd in `controller.log` (for example the model scaling up while pods were still starting,
  or repeated guardrail blocks).
- **Don't commit** `experiments/results/`, `setup.log`, `.env` or anything else generated.
  Only commit if Harsh asks, and never push to `main`.

## Teardown

```bash
./scripts/teardown_cluster.sh     # deletes the cluster; experiments/results/ stays
```
