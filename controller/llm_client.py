"""Thin wrapper around the Anthropic API: one prompt in, text out.

Features:
  * Reads ANTHROPIC_API_KEY (and optionally SCALE_MODEL) from the environment
    or from a .env file in the repo root.
  * temperature 0, a request timeout, and 2 automatic retries (done by the SDK).
  * Returns None instead of raising when the call fails, so a flaky network
    can't crash an experiment. The controller then falls back to NO_CHANGE.
  * A disk cache so re-running an experiment doesn't re-pay for identical calls.
    The cache key includes `repeat_index`, so `--repeats 3` makes 3 genuinely
    separate calls (and repeat 0 of a later run replays repeat 0 of an earlier one).
  * offline=True only replays the cache and never touches the network --
    useful for re-plotting or demoing without a key.
  * Every call's wall-clock latency is recorded. Cache hits report the latency
    of the original call, so replayed results match the original run.

You shouldn't need to edit this file to build the LLM controller.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import anthropic
from dotenv import load_dotenv

log = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-haiku-4-5-20251001"


@dataclass
class LLMResponse:
    text: str
    latency_s: float
    cached: bool
    model: str


class LLMClient:
    def __init__(
        self,
        model: str | None = None,
        temperature: float | None = 0.0,
        max_tokens: int = 400,
        timeout_s: float = 30.0,
        max_retries: int = 2,
        cache_dir: str | Path | None = None,
        offline: bool = False,
        repeat_index: int = 0,
    ):
        load_dotenv()  # pick up .env from the current directory (or a parent)
        self.model = model or os.getenv("SCALE_MODEL") or DEFAULT_MODEL
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.offline = offline
        self.repeat_index = repeat_index
        self._client: anthropic.Anthropic | None = None
        #: One entry per complete() call: {"latency_s", "cached", "ok", "error"}
        self.call_log: list[dict] = []

    # ------------------------------------------------------------------ public

    @staticmethod
    def has_api_key() -> bool:
        load_dotenv()
        return bool(os.getenv("ANTHROPIC_API_KEY", "").strip())

    def complete(
        self, system: str, prompt: str, use_cache: bool = True
    ) -> LLMResponse | None:
        """Send one prompt and return the text reply, or None on any failure."""
        key = self.cache_key(system, prompt)

        if use_cache:
            hit = self._read_cache(key)
            if hit is not None:
                self.call_log.append(
                    {
                        "latency_s": hit["latency_s"],
                        "cached": True,
                        "ok": True,
                        "error": "",
                    }
                )
                return LLMResponse(
                    hit["text"], hit["latency_s"], cached=True, model=self.model
                )

        if self.offline:
            return self._fail("offline mode and no cached response for this prompt")

        client = self._get_client()
        if client is None:
            return self._fail(
                "no ANTHROPIC_API_KEY (copy .env.example to .env and add your key)"
            )

        # The anthropic 1.x SDK no longer has a `temperature` argument, but the API
        # still honours it for Haiku 4.5 (our default), so we pass it via extra_body.
        # Newer models (e.g. Opus 4.7+) reject it -- use temperature=None for those.
        extra_body = (
            {"temperature": self.temperature} if self.temperature is not None else None
        )

        start = time.perf_counter()
        try:
            response = client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                extra_body=extra_body,
            )
        except anthropic.AuthenticationError:
            return self._fail(
                "authentication failed -- check ANTHROPIC_API_KEY in .env"
            )
        except anthropic.NotFoundError:
            return self._fail(
                f"model {self.model!r} not found -- check SCALE_MODEL in .env"
            )
        except anthropic.RateLimitError:
            return self._fail("rate limited (still failing after retries)")
        except anthropic.BadRequestError as e:
            hint = (
                " (this model may not accept temperature -- try LLMClient(temperature=None))"
                if "temperature" in str(e.message)
                else ""
            )
            return self._fail(f"bad request: {e.message}{hint}")
        except anthropic.APIStatusError as e:
            return self._fail(f"API error {e.status_code}: {e.message}")
        except anthropic.APIConnectionError as e:  # includes timeouts
            return self._fail(f"connection problem: {e}")
        latency = time.perf_counter() - start

        text = "".join(block.text for block in response.content if block.type == "text")
        self.call_log.append(
            {"latency_s": latency, "cached": False, "ok": True, "error": ""}
        )
        if use_cache:
            self._write_cache(key, system, prompt, text, latency, response.stop_reason)
        return LLMResponse(text, latency, cached=False, model=self.model)

    def cache_key(self, system: str, prompt: str) -> str:
        payload = json.dumps(
            [self.model, system, prompt, self.temperature, self.repeat_index],
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    # ----------------------------------------------------------------- private

    def _get_client(self) -> anthropic.Anthropic | None:
        if self._client is None:
            if not self.has_api_key():
                return None
            self._client = anthropic.Anthropic(
                timeout=self.timeout_s, max_retries=self.max_retries
            )
        return self._client

    def _fail(self, reason: str) -> None:
        log.warning("LLM call failed: %s", reason)
        self.call_log.append(
            {"latency_s": None, "cached": False, "ok": False, "error": reason}
        )

    def _cache_path(self, key: str) -> Path | None:
        return self.cache_dir / f"{key}.json" if self.cache_dir else None

    def _read_cache(self, key: str) -> dict | None:
        path = self._cache_path(key)
        if path is None or not path.exists():
            return None
        try:
            return json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            log.warning("ignoring unreadable cache file %s", path)
            return None

    def _write_cache(self, key, system, prompt, text, latency, stop_reason) -> None:
        path = self._cache_path(key)
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "model": self.model,
            "temperature": self.temperature,
            "repeat_index": self.repeat_index,
            "system": system,
            "prompt": prompt,
            "text": text,
            "latency_s": latency,
            "stop_reason": stop_reason,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        path.write_text(json.dumps(entry, indent=2, ensure_ascii=False))
