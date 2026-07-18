"""Sanitized Claude Code hook signals and conservative state transitions."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

from elchango.models import SessionState, StateConfidence


EXPECTED_TERMINAL_AFTER_MS = 10 * 60 * 1_000
SIGNAL_TTL_MS = 60 * 60 * 1_000
MAX_ACTIVITY_SIGNALS = 1_000
SUPPORTED_EVENTS = {
    "SessionStart",
    "UserPromptSubmit",
    "Notification",
    "Stop",
    "StopFailure",
    "SessionEnd",
}
WAITING_NOTIFICATIONS = {
    "permission_prompt",
    "idle_prompt",
    "elicitation_dialog",
    "agent_needs_input",
}


@dataclass(frozen=True, slots=True)
class ClaudeActivitySignal:
    """One sanitized official Claude Code lifecycle observation."""

    session_id: str
    event: str
    observed_at_ms: int
    state: SessionState
    confidence: StateConfidence
    detail: str


class ClaudeActivityStore:
    """Keep the latest official hook signal for each Claude Code session."""

    def __init__(
        self,
        *,
        terminal_deadline_ms: int = EXPECTED_TERMINAL_AFTER_MS,
        ttl_ms: int = SIGNAL_TTL_MS,
    ) -> None:
        if terminal_deadline_ms <= 0:
            raise ValueError("terminal_deadline_ms must be positive")
        if ttl_ms < terminal_deadline_ms:
            raise ValueError("ttl_ms must be at least terminal_deadline_ms")
        self._terminal_deadline_ms = terminal_deadline_ms
        self._ttl_ms = ttl_ms
        self._signals: dict[str, ClaudeActivitySignal] = {}
        self._lock = threading.Lock()

    def record(
        self,
        payload: dict[str, Any],
        observed_at_ms: int,
    ) -> ClaudeActivitySignal:
        """Validate one official hook payload without retaining message content."""

        event = payload.get("hook_event_name")
        if event not in SUPPORTED_EVENTS:
            raise ValueError(f"unsupported Claude Code hook event: {event!r}")
        session_id = payload.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("Claude Code hook event is missing session_id")
        cwd = payload.get("cwd")
        if not isinstance(cwd, str) or not cwd:
            raise ValueError("Claude Code hook event is missing cwd")
        transcript_path = payload.get("transcript_path")
        if not isinstance(transcript_path, str) or not transcript_path:
            raise ValueError("Claude Code hook event is missing transcript_path")

        state, confidence, detail = _event_state(
            event,
            payload.get("notification_type"),
        )
        signal = ClaudeActivitySignal(
            session_id=session_id,
            event=event,
            observed_at_ms=observed_at_ms,
            state=state,
            confidence=confidence,
            detail=detail,
        )
        with self._lock:
            self._purge_expired_locked(observed_at_ms)
            if (
                session_id not in self._signals
                and len(self._signals) >= MAX_ACTIVITY_SIGNALS
            ):
                oldest = min(
                    self._signals,
                    key=lambda candidate: self._signals[candidate].observed_at_ms,
                )
                self._signals.pop(oldest, None)
            self._signals[session_id] = signal
        return signal

    def state_for(
        self,
        session_id: str,
        observed_at_ms: int,
    ) -> tuple[SessionState, StateConfidence, str] | None:
        """Return fresh evidence or explicitly degrade a missing terminal event."""

        with self._lock:
            signal = self._signals.get(session_id)
            if signal is None:
                return None
            age_ms = observed_at_ms - signal.observed_at_ms
            if age_ms > self._ttl_ms:
                self._signals.pop(session_id, None)
                return None
            if (
                signal.state in {"working", "waiting"}
                and age_ms > self._terminal_deadline_ms
            ):
                return (
                    "unknown",
                    "unknown",
                    f"Claude Code {signal.event} signal is stale; "
                    "expected terminal event was not observed",
                )
            return signal.state, signal.confidence, signal.detail

    def _purge_expired_locked(self, observed_at_ms: int) -> None:
        expired = [
            session_id
            for session_id, signal in self._signals.items()
            if observed_at_ms - signal.observed_at_ms > self._ttl_ms
        ]
        for session_id in expired:
            self._signals.pop(session_id, None)


def _event_state(
    event: object,
    notification_type: object,
) -> tuple[SessionState, StateConfidence, str]:
    if event == "UserPromptSubmit":
        return "working", "observed", "Claude Code prompt submitted"
    if event == "Stop":
        return "done", "observed", "Claude Code turn completed"
    if event == "StopFailure":
        return "error", "observed", "Claude Code turn failed"
    if event == "SessionEnd":
        return "idle", "observed", "Claude Code session ended"
    if event == "SessionStart":
        return "idle", "observed", "Claude Code session started"
    if notification_type in WAITING_NOTIFICATIONS:
        return (
            "waiting",
            "observed",
            f"Claude Code is waiting: {notification_type}",
        )
    if not isinstance(notification_type, str) or not notification_type:
        raise ValueError("Claude Code Notification is missing notification_type")
    return (
        "idle",
        "candidate",
        f"Claude Code notification does not prove a deck state: {notification_type}",
    )
