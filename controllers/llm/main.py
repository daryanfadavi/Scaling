"""Long-running plugin loop. The experiment runner starts this via start.sh.

Observe only through telemetry.latest_snapshot. Scale only after LLMAutoscaler
has validated the replica count. SIGTERM stops the loop.
"""

from __future__ import annotations

import signal
import subprocess
import sys
import time

from controller.fake_llm_client import FakeLLMClient
from controller.llm_client import LLMClient
from controllers.llm.config import load_config
from controllers.llm.controller import LLMAutoscaler
from controllers.llm.logging import emit
from telemetry import latest_snapshot

_stop = False


def _handle_stop(signum, frame) -> None:
    del signum, frame
    global _stop
    _stop = True


def make_client(config):
    if config.fake_llm:
        return FakeLLMClient()
    return LLMClient(timeout_s=config.llm_timeout_seconds, max_retries=config.llm_max_retries)


def scale_to(config, replicas: int) -> None:
    cmd = [
        "kubectl", "--context", config.kube_context,
        "-n", config.namespace,
        "scale", f"deployment/{config.deployment}",
        f"--replicas={int(replicas)}",
    ]
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(detail or f"kubectl scale failed ({proc.returncode})")


def run_once(controller: LLMAutoscaler, config, snapshot: dict | None = None):
    snap = latest_snapshot() if snapshot is None else snapshot
    result = controller.decide(snap)
    if (
        not config.dry_run
        and result.current_desired is not None
        and result.validated_desired != result.current_desired
    ):
        try:
            scale_to(config, result.validated_desired)
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
            result.apply_error = str(exc)
        else:
            result.applied = True
            controller.note_applied(result, time.time())
    if config.log_decisions:
        emit(result)
    return result


def main() -> int:
    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)
    config = load_config()
    controller = LLMAutoscaler(config, make_client(config))
    while not _stop:
        started = time.monotonic()
        try:
            run_once(controller, config)
        except Exception as exc:  # query failure: do not invent metrics or scale
            sys.stderr.write(f"observation failed: {exc}\n")
            sys.stderr.flush()
        remaining = config.interval_seconds - (time.monotonic() - started)
        end = time.monotonic() + max(0.0, remaining)
        while not _stop and time.monotonic() < end:
            time.sleep(min(0.5, end - time.monotonic()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
