#!/usr/bin/env bash
# Long-running LLM controller. The experiment runner sends SIGTERM to stop it.
cd "$(dirname "$0")/../.."
exec python -m controllers.llm.main
