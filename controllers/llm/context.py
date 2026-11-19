"""Operator notes and optional structured events.

Notes are not commands. Disabled modes receive nothing from this module,
including notes that happen to be set in the environment.
"""

from __future__ import annotations

import time

from controllers.llm.models import OperatorEvent, finite_float


class ContextManager:
    def __init__(self, notes: tuple[str, ...], event: dict | None, received_at: float | None = None):
        received_at = time.time() if received_at is None else received_at
        self._notes = tuple(notes)
        self._event = _parse_event(event, received_at) if event else None
        self._conflict = False
        if self._event and _notes_conflict(self._notes, self._event):
            self._event.conflict = True
            self._conflict = True

    @property
    def conflict(self) -> bool:
        return self._conflict

    def visible(self, now: float, *, enabled: bool) -> tuple[tuple[str, ...], OperatorEvent | None]:
        if not enabled:
            return (), None
        event = self._event
        if event is not None and _expired(event, now):
            event = None
        return self._notes, event


def _parse_event(raw: dict, received_at: float) -> OperatorEvent:
    return OperatorEvent(
        event_type=str(raw.get("event_type") or "note"),
        expected_start_seconds=finite_float(raw.get("expected_start_seconds")),
        expected_duration_seconds=finite_float(raw.get("expected_duration_seconds")),
        expected_rps=finite_float(raw.get("expected_rps")),
        note=str(raw.get("note") or ""),
        received_at=received_at,
    )


def _expired(event: OperatorEvent, now: float) -> bool:
    if event.expected_start_seconds is None or event.expected_duration_seconds is None:
        # No validity window: keep the note text, but do not treat it as a timed event.
        return False
    end = event.received_at + event.expected_start_seconds + event.expected_duration_seconds
    return now > end


def _notes_conflict(notes: tuple[str, ...], event: OperatorEvent) -> bool:
    """A note that names a very different request rate than the structured event."""
    if event.expected_rps is None:
        return False
    for note in notes:
        for token in note.replace(",", " ").split():
            try:
                number = float(token)
            except ValueError:
                continue
            if number > 10 and abs(number - event.expected_rps) / event.expected_rps > 0.5:
                return True
    return False
