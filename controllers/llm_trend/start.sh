#!/usr/bin/env bash
# Same controller as controllers/llm, with prompt variant cluster-v2-trend.
# Started by experiments/run_experiment.py (see controllers/README.md); output goes to controller.log.
set -euo pipefail
cd "$(dirname "$0")/../.."
export PYTHONDONTWRITEBYTECODE=1   # the runner hashes this folder; bytecode would change the hash
export SCALE_PROMPT=cluster-v2-trend
exec python -m controllers.llm.main
