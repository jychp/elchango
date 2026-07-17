"""Map provider sessions onto the fixed 3 by 5 v0.1 deck."""

from __future__ import annotations

import threading
from pathlib import Path

from elchango.models import DeckButton, DeckSnapshot, ProviderSnapshot
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

    def snapshot(self) -> DeckSnapshot:
        provider_snapshot = self._provider.snapshot()
        signature = (
            provider_snapshot.selected_session_id,
            provider_snapshot.sessions,
            provider_snapshot.source,
            provider_snapshot.read_only,
        )
        with self._lock:
            if signature != self._signature:
                self._revision += 1
                self._signature = signature
            revision = self._revision

        buttons = _build_buttons(provider_snapshot)
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


def _build_buttons(
    snapshot: ProviderSnapshot,
) -> list[DeckButton]:
    visible_sessions = snapshot.sessions[:SESSION_SLOTS]
    buttons = [
        DeckButton(
            id=f"session:{session.id}",
            position=position,
            kind="session",
            label=session.title,
            detail=_session_detail(session.workspace_path),
            icon="repo",
            color=session.state,
            selected=session.selected,
            enabled=False,
            confidence=session.confidence,
            session_id=session.id,
        )
        for position, session in enumerate(visible_sessions)
    ]

    while len(buttons) < SESSION_SLOTS:
        position = len(buttons)
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
            id="control:focus",
            position=11,
            kind="control",
            label="Focus",
            detail="Target required",
            icon="action",
            color="control",
            selected=False,
            enabled=False,
            confidence="observed",
            action="focus_session",
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
