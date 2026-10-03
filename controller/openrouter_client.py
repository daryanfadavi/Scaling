"""OpenRouter client: the same `complete(system, prompt)` as LLMClient, but the
call goes to OpenRouter, which serves models from many vendors behind one API.

Use it to run the LLM controllers against non-Anthropic models:

    python -m experiments.run --controller llm_context --scenario spike_with_warning \\
        --provider openrouter --model meta-llama/llama-3.3-70b-instruct

Everything except the network call is inherited from LLMClient: the disk cache,
offline replay, the call log, and "return None instead of raising".

What's different from LLMClient:
  * Reads OPENROUTER_API_KEY and OPENROUTER_MODEL from the environment or .env.
    There is no default model -- you must name one, so every run states exactly
    which model it used. SCALE_MODEL is ignored (it holds an Anthropic model id).
  * UPSTREAM PINNING. OpenRouter can route the same model id to different
    hosting companies ("upstreams") from one call to the next, and they don't
    all behave identically. For reproducible experiments, pin one with
    `upstream=["Fireworks"]` / OPENROUTER_UPSTREAM / --openrouter-upstream.
    A pinned client never falls back to another upstream.
  * The cache key includes "openrouter" and the pinned upstream, so entries can
    never be mixed up with Anthropic ones or with another upstream's.
  * An empty reply counts as a failed call and is not cached, so a one-off
    upstream glitch can't be replayed forever by --offline.

Note for comparing latency: `latency_s` includes the extra hop through
OpenRouter, so it is not comparable with direct Anthropic calls.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from pathlib import Path

import httpx2

from controller.llm_client import LLMCallError, LLMClient

log = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
#: Wait this long before retry 1, twice as long before retry 2, ...
RETRY_BACKOFF_S = 1.0


class _RetryableError(LLMCallError):
    """A failure worth retrying: rate limit, server error, or network problem."""


def parse_upstream(text: str | None) -> list[str] | None:
    """'Fireworks, Together' -> ['Fireworks', 'Together']; empty -> None (unpinned)."""
    names = [name.strip() for name in (text or "").split(",") if name.strip()]
    return names or None


class OpenRouterClient(LLMClient):
    provider_name = "openrouter"
    api_key_env = "OPENROUTER_API_KEY"

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
        upstream: list[str] | None = None,
    ):
        super().__init__(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_s=timeout_s,
            max_retries=max_retries,
            cache_dir=cache_dir,
            offline=offline,
            repeat_index=repeat_index,
        )
        # Replace LLMClient's choice: no SCALE_MODEL, no default. None means
        # "not configured", and _call_api fails with a clear message.
        self.model = model or os.getenv("OPENROUTER_MODEL", "").strip() or None
        #: Upstream provider names to pin, in order of preference; None = unpinned.
        self.upstream = upstream or parse_upstream(os.getenv("OPENROUTER_UPSTREAM"))
        self._http: httpx2.Client | None = None

        if self.upstream is None and not offline:
            log.warning(
                "OpenRouter upstream is not pinned: the same model may be served by "
                "different providers between calls, so runs may not be reproducible. "
                "Set OPENROUTER_UPSTREAM or pass --openrouter-upstream."
            )

    def cache_key(self, system: str, prompt: str) -> str:
        payload = json.dumps(
            [
                self.provider_name,
                self.upstream,
                self.model,
                system,
                prompt,
                self.temperature,
                self.repeat_index,
            ],
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    # ----------------------------------------------------------------- private

    def _call_api(self, system: str, prompt: str) -> tuple[str, str | None]:
        if not self.model:
            raise LLMCallError("no model -- pass --model or set OPENROUTER_MODEL in .env")
        if not self.has_api_key():
            raise LLMCallError(
                "no OPENROUTER_API_KEY (copy .env.example to .env and add your key)"
            )

        headers = {"Authorization": f"Bearer {os.environ[self.api_key_env].strip()}"}
        body: dict = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": self.max_tokens,
        }
        if self.temperature is not None:
            body["temperature"] = self.temperature
        if self.upstream is not None:
            body["provider"] = {"order": self.upstream, "allow_fallbacks": False}

        # The Anthropic SDK retries for us; here we do the same by hand.
        failure = "no attempt was made"
        for attempt in range(self.max_retries + 1):
            if attempt > 0:
                time.sleep(RETRY_BACKOFF_S * attempt)
            try:
                return self._post_once(headers, body)
            except _RetryableError as e:
                failure = str(e)
        raise LLMCallError(failure)

    def _post_once(self, headers: dict, body: dict) -> tuple[str, str | None]:
        """One HTTP request. Raises _RetryableError if trying again might help,
        plain LLMCallError if it won't."""
        try:
            response = self._get_http().post(OPENROUTER_URL, headers=headers, json=body)
        except httpx2.TransportError as e:  # includes timeouts
            raise _RetryableError(f"connection problem: {e!r}") from None

        try:
            data = response.json()
        except ValueError:
            data = None
        if not isinstance(data, dict):
            data = {}

        # Errors arrive either as an HTTP status or (for upstream problems) as an
        # "error" object inside a 200 reply, at the top level or in the choice.
        choices = data.get("choices")
        choice = choices[0] if isinstance(choices, list) and choices else {}
        if not isinstance(choice, dict):
            choice = {}
        error = data.get("error") or choice.get("error")
        if response.status_code != 200 or error:
            if not isinstance(error, dict):
                error = {}
            code = error.get("code")
            if response.status_code != 200 or not isinstance(code, int):
                code = response.status_code
            self._raise_for_error(code, str(error.get("message") or "no details"))

        message = choice.get("message")
        text = message.get("content") if isinstance(message, dict) else None
        finish_reason = choice.get("finish_reason")
        if not isinstance(text, str) or not text.strip():
            raise LLMCallError(f"empty reply (finish_reason={finish_reason})")

        log.info("OpenRouter served %s via %s", self.model, data.get("provider"))
        return text, finish_reason

    def _raise_for_error(self, code: int, message: str) -> None:
        if code == 401:
            raise LLMCallError(
                "authentication failed -- check OPENROUTER_API_KEY in .env"
            )
        if code == 402:
            raise LLMCallError(f"out of OpenRouter credits: {message}")
        if code == 404 or (code == 400 and "model" in message.lower()):
            raise LLMCallError(
                f"model {self.model!r} not found, or not served by the pinned upstream "
                f"-- check --model / OPENROUTER_MODEL ({message})"
            )
        if code == 429:
            raise _RetryableError("rate limited (still failing after retries)")
        if code >= 500:
            raise _RetryableError(f"API error {code}: {message}")
        raise LLMCallError(f"API error {code}: {message}")

    def _get_http(self) -> httpx2.Client:
        if self._http is None:
            self._http = httpx2.Client(timeout=self.timeout_s)
        return self._http
