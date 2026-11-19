#!/usr/bin/env bash
# The folder name is the result label. These two lines override the parent environment.
cd "$(dirname "$0")/../.."
export SCALE_LLM_MODE=llm
export SCALE_LLM_FAKE=1
exec python -m controllers.llm.main
