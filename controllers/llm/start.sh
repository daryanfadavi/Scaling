#!/usr/bin/env bash
# Started by experiments/run_experiment.py as a background process (see controllers/README.md).
# stdout and stderr go to controller.log in the run's results folder.
set -euo pipefail

# Run from the repo root so `controllers`, `controller` and `telemetry` import.
cd "$(dirname "$0")/../.."

# Don't write .pyc files into controllers/llm/: the runner hashes every file in
# this folder into metadata.json, and bytecode would change that hash.
export PYTHONDONTWRITEBYTECODE=1

# `exec` so the Python process receives the runner's SIGTERM directly.
exec python -m controllers.llm.main
