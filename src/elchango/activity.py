"""Short-lived Cursor hook signals used to complement SQLite snapshots."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

from elchango.models import SessionState, StateConfidence


SIGNAL_TTL_MS = 60 * 60 * 1_000
SUPPORTED_EVENTS = {
    "sessionStart",
    "beforeSubmitPrompt",
    "stop",
    "sessionEnd",
}


@dataclass(frozen=True, slots=True)
class ActivitySignal:
    """One sanitized lifecycle observation for a Cursor conversation."""

    session_id: str
    event: str
    observed_at_ms: int
    state: SessionState
    confidence: StateConfidence
    detail: str


class ActivityStore:
    """Keep only the latest in-memory lifecycle signal per session."""

    def __init__(self, ttl_ms: int = SIGNAL_TTL_MS) -> None:
        self._ttl_ms = ttl_ms
        self._signals: dict[str, ActivitySignal] = {}
        self._acknowledged_at_ms: dict[str, int] = {}
        self._selected_session_id: str | None = None
        self._selection_initialized = False
        self._lock = threading.Lock()

    def record(self, payload: dict[str, Any], observed_at_ms: int) -> ActivitySignal:
        event = payload.get("hook_event_name")
        session_id = payload.get("conversation_id")
        if event not in SUPPORTED_EVENTS:
            raise ValueError(f"unsupported Cursor hook event: {event!r}")
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("Cursor hook event is missing conversation_id")

        state, confidence, detail = _event_state(event, payload.get("status"))
        signal = ActivitySignal(
            session_id=session_id,
            event=event,
            observed_at_ms=observed_at_ms,
            state=state,
            confidence=confidence,
            detail=detail,
        )
        with self._lock:
            self._signals[session_id] = signal
        return signal

    def state_for(
        self,
        session_id: str,
        observed_at_ms: int,
    ) -> tuple[SessionState, StateConfidence, str] | None:
        with self._lock:
            signal = self._signals.get(session_id)
            if signal is None:
                return None
            if observed_at_ms - signal.observed_at_ms > self._ttl_ms:
                del self._signals[session_id]
                return None
            acknowledged_at_ms = self._acknowledged_at_ms.get(session_id)
            if (
                signal.state == "done"
                and acknowledged_at_ms is not None
                and acknowledged_at_ms >= signal.observed_at_ms
            ):
                return "idle", "observed", "completion acknowledged by focus"
        return signal.state, signal.confidence, signal.detail

    def observe_selection(
        self,
        session_id: str | None,
        observed_at_ms: int,
    ) -> None:
        """Acknowledge completion only when selection changes after startup."""

        with self._lock:
            if not self._selection_initialized:
                self._selected_session_id = session_id
                self._selection_initialized = True
                return
            if session_id == self._selected_session_id:
                return
            self._selected_session_id = session_id
            if session_id is not None:
                self._acknowledged_at_ms[session_id] = observed_at_ms

    def acknowledge(self, session_id: str, observed_at_ms: int) -> None:
        """Acknowledge one completed session after an explicit deck focus."""

        with self._lock:
            self._acknowledged_at_ms[session_id] = observed_at_ms


def _event_state(
    event: str,
    status: object,
) -> tuple[SessionState, StateConfidence, str]:
    if event == "beforeSubmitPrompt":
        return "working", "observed", "Cursor prompt submitted"
    if event == "stop":
        if status == "error":
            return "error", "observed", "Cursor agent stopped with error"
        if status == "completed":
            return "done", "observed", "Cursor agent completed"
        if status == "aborted":
            return "idle", "observed", "Cursor agent aborted"
        raise ValueError(f"unsupported Cursor stop status: {status!r}")
    if event == "sessionEnd":
        return "idle", "observed", "Cursor session ended"
    return "idle", "observed", "Cursor session started"
