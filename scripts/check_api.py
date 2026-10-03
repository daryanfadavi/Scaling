"""Check that your Anthropic API key works: makes ONE small API call.

    python scripts/check_api.py

Uses the same LLMClient as the experiments (model from SCALE_MODEL in .env,
default claude-haiku-4-5-20251001). Bypasses the cache, so it really calls the API.
Exit code 0 = working, 1 = not working (with a hint why).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent)
)  # so `controller` imports work

from controller.llm_client import LLMClient


def main() -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    if not LLMClient.has_api_key():
        print(
            "No ANTHROPIC_API_KEY found.\n"
            "  1. cp .env.example .env\n"
            "  2. put your key after ANTHROPIC_API_KEY= in .env\n"
            "  3. run this script again\n"
            "(Everything except the LLM controllers works without a key.)"
        )
        return 1

    client = LLMClient()
    print(f"Calling {client.model} ...")
    response = client.complete(
        system="You are a connectivity check. Follow the instruction exactly.",
        prompt='Reply with exactly this JSON and nothing else: {"ok": true}',
        use_cache=False,
    )
    if response is None:
        print("FAILED -- see the warning above for the reason.")
        return 1

    print(
        f"OK  model={response.model}  latency={response.latency_s:.2f}s  reply={response.text.strip()!r}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
