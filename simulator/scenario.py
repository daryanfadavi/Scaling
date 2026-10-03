"""Load a scenario (workload + operator notes + env overrides) from YAML.

Scenarios are experiment config: the environment reads them, controllers never
do. A controller only ever sees the Observation the environment builds.

Example YAML:

    name: spike
    description: Base 100 req/s, jumps to 500 at tick 30 for 20 ticks.
    ticks: 80
    load:
      type: spike        # one of simulator.workloads.WORKLOADS
      base: 100
      peak: 500
      start_tick: 30
      duration_ticks: 20
    operator_notes:      # optional
      - start_tick: 24
        end_tick: 30     # optional; omit to keep the note active until the end
        text: "Marketing campaign launches in ~90 seconds ..."
    env:                 # optional overrides of simulator.config.SimConfig
      initial_replicas: 2
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class OperatorNote:
    start_tick: int
    text: str
    end_tick: int | None = None  # None = active until the end of the scenario

    def is_active(self, tick: int) -> bool:
        return self.start_tick <= tick and (
            self.end_tick is None or tick < self.end_tick
        )


@dataclass(frozen=True)
class Scenario:
    name: str
    load: dict[str, Any]
    ticks: int = 80
    description: str = ""
    operator_notes: list[OperatorNote] = field(default_factory=list)
    env: dict[str, Any] = field(default_factory=dict)


def load_scenario(path: str | Path) -> Scenario:
    path = Path(path)
    with path.open() as f:
        raw = yaml.safe_load(f)

    notes = [
        OperatorNote(
            start_tick=n["start_tick"], text=n["text"], end_tick=n.get("end_tick")
        )
        for n in raw.get("operator_notes") or []
    ]
    return Scenario(
        name=raw.get("name", path.stem),
        description=raw.get("description", ""),
        ticks=raw.get("ticks", 80),
        load=raw["load"],
        operator_notes=notes,
        env=raw.get("env") or {},
    )
