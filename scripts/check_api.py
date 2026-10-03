"""Check that your LLM API key works: makes ONE small API call.

    python scripts/check_api.py                                    # Anthropic
    python scripts/check_api.py --provider openrouter --model meta-llama/llama-3.3-70b-instruct

Uses the same client as the experiments. For Anthropic the model comes from
SCALE_MODEL in .env (default claude-haiku-4-5-20251001); for OpenRouter from
--model or OPENROUTER_MODEL (there is no default). Bypasses the cache, so it
really calls the API.
Exit code 0 = working, 1 = not working (with a hint why).
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent)
)  # so `controller` imports work

from controller.llm_client import LLMClient
from controller.openrouter_client import OpenRouterClient

CLIENTS = {"anthropic": LLMClient, "openrouter": OpenRouterClient}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Make one small LLM API call.")
    parser.add_argument("--provider", choices=sorted(CLIENTS), default="anthropic")
    parser.add_argument("--model", help="model id (default: from .env)")
    args = parser.parse_args(argv)

    # ERROR, not WARNING: the "upstream is not pinned" warning is noise here.
    logging.basicConfig(level=logging.ERROR, format="%(levelname)s %(message)s")
    logging.getLogger("controller.llm_client").setLevel(logging.WARNING)

    client_class = CLIENTS[args.provider]
    key_name = client_class.api_key_env
    if not client_class.has_api_key():
        print(
            f"No {key_name} found.\n"
            "  1. cp .env.example .env\n"
            f"  2. put your key after {key_name}= in .env\n"
            "  3. run this script again\n"
            "(Everything except the LLM controllers works without a key.)"
        )
        return 1

    client = client_class(model=args.model)
    if not client.model:
        print(
            "No model chosen. OpenRouter has no default: pass --model <id> "
            "or set OPENROUTER_MODEL in .env."
        )
        return 1

    print(f"Calling {client.model} via {client.provider_name} ...")
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
