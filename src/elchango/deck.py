"""Map provider sessions onto the fixed 3 by 5 v0.1 deck."""

from __future__ import annotations

import threading
from pathlib import Path

from elchango.models import AgentSession, DeckButton, DeckSnapshot, ProviderSnapshot
from elchango.providers.base import AgentProvider


SESSION_SLOTS = 10
TOTAL_BUTTONS = 15


class DeckService:
    """Build stable, versioned render snapshots from one provider."""

    def __init__(self, provider: AgentProvider) -> None:
        self._provider = provider
        self._revision = 0
        self._signature: object | None = None
        self._lock = threading.Lock()
        self._session_slots: list[str | None] = [None] * SESSION_SLOTS

    def snapshot(self) -> DeckSnapshot:
        provider_snapshot = self._provider.snapshot()
        with self._lock:
            visible_sessions = self._assign_session_slots(provider_snapshot)
            signature = (
                provider_snapshot.selected_session_id,
                provider_snapshot.sessions,
                tuple(self._session_slots),
                provider_snapshot.source,
                provider_snapshot.read_only,
            )
            if signature != self._signature:
                self._revision += 1
                self._signature = signature
            revision = self._revision

        buttons = _build_buttons(visible_sessions)
        if len(buttons) != TOTAL_BUTTONS:
            raise RuntimeError(
                f"Deck invariant violated: expected {TOTAL_BUTTONS} buttons, "
                f"got {len(buttons)}"
            )
        return DeckSnapshot(
            revision=revision,
            observed_at_ms=provider_snapshot.observed_at_ms,
            source=provider_snapshot.source,
            read_only=provider_snapshot.read_only,
            selected_session_id=provider_snapshot.selected_session_id,
            buttons=tuple(buttons),
        )

    def _assign_session_slots(
        self,
        snapshot: ProviderSnapshot,
    ) -> tuple[AgentSession | None, ...]:
        sessions_by_id = {session.id: session for session in snapshot.sessions}
        for index, session_id in enumerate(self._session_slots):
            if session_id not in sessions_by_id:
                self._session_slots[index] = None

        selected_id = snapshot.selected_session_id
        if selected_id in sessions_by_id and selected_id not in self._session_slots:
            try:
                selected_slot = self._session_slots.index(None)
            except ValueError:
                def slot_updated_at(index: int) -> int:
                    session_id = self._session_slots[index]
                    if session_id is None:
                        return -1
                    return sessions_by_id[session_id].updated_at_ms

                selected_slot = min(
                    range(SESSION_SLOTS),
                    key=slot_updated_at,
                )
            self._session_slots[selected_slot] = selected_id

        for session in snapshot.sessions:
            if session.id in self._session_slots:
                continue
            try:
                empty_slot = self._session_slots.index(None)
            except ValueError:
                break
            self._session_slots[empty_slot] = session.id

        return tuple(
            sessions_by_id.get(session_id) if session_id is not None else None
            for session_id in self._session_slots
        )


def _build_buttons(
    sessions: tuple[AgentSession | None, ...],
) -> list[DeckButton]:
    buttons: list[DeckButton] = []
    for position, session in enumerate(sessions):
        if session is None:
            buttons.append(
                DeckButton(
                    id=f"empty:{position}",
                    position=position,
                    kind="empty",
                    label="Available",
                    detail="No session",
                    icon="plus",
                    color="unknown",
                    selected=False,
                    enabled=False,
                    confidence="unknown",
                )
            )
            continue
        buttons.append(
            DeckButton(
                id=f"session:{session.id}",
                position=position,
                kind="session",
                label=session.title,
                detail=_session_detail(session.workspace_path),
                icon="repo",
                color=session.state,
                selected=session.selected,
                enabled=True,
                confidence=session.confidence,
                session_id=session.id,
            )
        )

    controls = (
        DeckButton(
            id="control:new",
            position=10,
            kind="control",
            label="New",
            detail="Coming in S5",
            icon="plus",
            color="control",
            selected=False,
            enabled=False,
            confidence="observed",
            action="new_session",
        ),
        DeckButton(
            id="empty:11",
            position=11,
            kind="empty",
            label="Available",
            detail="No action",
            icon="plus",
            color="unknown",
            selected=False,
            enabled=False,
            confidence="unknown",
        ),
        DeckButton(
            id="control:primary",
            position=12,
            kind="control",
            label="Action 1",
            detail="Target required",
            icon="action",
            color="control",
            selected=False,
            enabled=False,
            confidence="observed",
            action="primary_action",
        ),
        DeckButton(
            id="control:secondary",
            position=13,
            kind="control",
            label="Action 2",
            detail="Target required",
            icon="action",
            color="control",
            selected=False,
            enabled=False,
            confidence="observed",
            action="secondary_action",
        ),
        DeckButton(
            id="control:stop",
            position=14,
            kind="control",
            label="Stop",
            detail="Target required",
            icon="action",
            color="control",
            selected=False,
            enabled=False,
            confidence="observed",
            action="stop_session",
        ),
    )
    buttons.extend(controls)
    return buttons


def _session_detail(workspace_path: str | None) -> str:
    if workspace_path is None:
        return "Unknown workspace"
    name = Path(workspace_path).name
    return name or workspace_path
