"""Short-lived Cursor hook signals used to complement SQLite snapshots."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

from elchango.models import SessionState, StateConfidence


SIGNAL_TTL_MS = 60 * 60 * 1_000
MAX_ACTIVITY_SIGNALS = 1_000
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
    generation_id: str | None
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
        self._composer_modes: dict[str, str] = {}
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
        generation_id = payload.get("generation_id")
        if generation_id is not None and (
            not isinstance(generation_id, str) or not generation_id
        ):
            raise ValueError("Cursor hook generation_id must be a non-empty string")
        composer_mode = payload.get("composer_mode")
        if composer_mode is not None and composer_mode not in {"agent", "plan"}:
            raise ValueError(f"unsupported Cursor composer mode: {composer_mode!r}")
        with self._lock:
            self._purge_expired_locked(observed_at_ms)
            if (
                session_id not in self._signals
                and len(self._signals) >= MAX_ACTIVITY_SIGNALS
            ):
                oldest_session_id = min(
                    self._signals,
                    key=lambda candidate: self._signals[candidate].observed_at_ms,
                )
                self._remove_session_locked(oldest_session_id)
            if event == "beforeSubmitPrompt":
                if isinstance(composer_mode, str):
                    self._composer_modes[session_id] = composer_mode
                else:
                    self._composer_modes.pop(session_id, None)
            turn_mode = self._composer_modes.get(session_id)
            state, confidence, detail = _event_state(
                event,
                payload.get("status"),
                turn_mode,
            )
            signal = ActivitySignal(
                session_id=session_id,
                generation_id=generation_id,
                event=event,
                observed_at_ms=observed_at_ms,
                state=state,
                confidence=confidence,
                detail=detail,
            )
            self._signals[session_id] = signal
            if event == "sessionEnd":
                self._composer_modes.pop(session_id, None)
        return signal

    def state_for(
        self,
        session_id: str,
        observed_at_ms: int,
        current_generation_id: str | None = None,
    ) -> tuple[SessionState, StateConfidence, str] | None:
        with self._lock:
            signal = self._signals.get(session_id)
            if signal is None:
                return None
            if observed_at_ms - signal.observed_at_ms > self._ttl_ms:
                self._remove_session_locked(session_id)
                return None
            if (
                signal.state in {"done", "waiting"}
                and signal.generation_id is not None
                and current_generation_id is not None
                and signal.generation_id != current_generation_id
            ):
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

    def _purge_expired_locked(self, observed_at_ms: int) -> None:
        expired = [
            session_id
            for session_id, signal in self._signals.items()
            if observed_at_ms - signal.observed_at_ms > self._ttl_ms
        ]
        for session_id in expired:
            self._remove_session_locked(session_id)

    def _remove_session_locked(self, session_id: str) -> None:
        self._signals.pop(session_id, None)
        self._composer_modes.pop(session_id, None)
        self._acknowledged_at_ms.pop(session_id, None)


def _event_state(
    event: str,
    status: object,
    composer_mode: str | None,
) -> tuple[SessionState, StateConfidence, str]:
    if event == "beforeSubmitPrompt":
        return "working", "observed", "Cursor prompt submitted"
    if event == "stop":
        if status == "error":
            return "waiting", "observed", "Cursor agent stopped with error"
        if status == "completed":
            if composer_mode == "plan":
                return "waiting", "observed", "Cursor plan awaiting approval"
            return "done", "observed", "Cursor agent completed"
        if status == "aborted":
            return "idle", "observed", "Cursor agent aborted"
        raise ValueError(f"unsupported Cursor stop status: {status!r}")
    if event == "sessionEnd":
        return "idle", "observed", "Cursor session ended"
    return "idle", "observed", "Cursor session started"
