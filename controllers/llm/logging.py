"""One JSON object per decision, on stdout (the runner saves that as controller.log)."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict

from controllers.llm.models import CycleResult


def emit(result: CycleResult) -> None:
    payload = asdict(result)
    sys.stdout.write(json.dumps(payload, default=str) + "\n")
    sys.stdout.flush()
