"""Map provider sessions onto the fixed 3 by 5 v0.1 deck."""

from __future__ import annotations

import threading
from pathlib import Path

from elchango.models import (
    AgentSession,
    ButtonColor,
    DeckButton,
    DeckSnapshot,
    ProviderSnapshot,
    SessionState,
)
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
        self._session_order: list[str | None] = []
        self._page_index = 0
        self._refresh_requested = True

    def snapshot(self) -> DeckSnapshot:
        provider_snapshot = self._provider.snapshot()
        with self._lock:
            visible_sessions = self._visible_sessions(provider_snapshot)
            page_count = self._page_count()
            has_previous = self._page_index > 0
            has_next = self._page_index + 1 < page_count
            page = self._page_index + 1
            signature = (
                provider_snapshot.selected_session_id,
                provider_snapshot.sessions,
                tuple(self._session_order),
                self._page_index,
                provider_snapshot.source,
                provider_snapshot.read_only,
            )
            if signature != self._signature:
                self._revision += 1
                self._signature = signature
            revision = self._revision

        buttons = _build_buttons(
            visible_sessions,
            page=page,
            has_next=has_next,
        )
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
            page=page,
            page_count=page_count,
            has_previous=has_previous,
            has_next=has_next,
            buttons=tuple(buttons),
        )

    def refresh(self) -> DeckSnapshot:
        with self._lock:
            self._refresh_requested = True
            self._page_index = 0
        return self.snapshot()

    def previous_page(self) -> DeckSnapshot:
        with self._lock:
            if self._page_index <= 0:
                raise ValueError("the deck is already on the first page")
            self._page_index -= 1
        return self.snapshot()

    def next_page(self) -> DeckSnapshot:
        with self._lock:
            if self._page_index + 1 >= self._page_count():
                raise ValueError("the deck is already on the last page")
            self._page_index += 1
        return self.snapshot()

    def _visible_sessions(
        self,
        snapshot: ProviderSnapshot,
    ) -> tuple[AgentSession | None, ...]:
        sessions_by_id = {session.id: session for session in snapshot.sessions}
        ordered_sessions = sorted(
            snapshot.sessions,
            key=lambda session: (-session.last_activity_at_ms, session.id),
        )
        if self._refresh_requested:
            self._session_order = [session.id for session in ordered_sessions]
            self._refresh_requested = False
        else:
            for index, session_id in enumerate(self._session_order):
                if session_id is not None and session_id not in sessions_by_id:
                    self._session_order[index] = None
            known_ids = {
                session_id
                for session_id in self._session_order
                if session_id is not None
            }
            self._session_order.extend(
                session.id
                for session in ordered_sessions
                if session.id not in known_ids
            )

        self._page_index = min(self._page_index, self._page_count() - 1)
        start = self._page_index * SESSION_SLOTS
        page_ids = self._session_order[start : start + SESSION_SLOTS]
        page_ids.extend([None] * (SESSION_SLOTS - len(page_ids)))
        return tuple(
            sessions_by_id.get(session_id) if session_id is not None else None
            for session_id in page_ids
        )

    def _page_count(self) -> int:
        return max(1, (len(self._session_order) + SESSION_SLOTS - 1) // SESSION_SLOTS)


def _build_buttons(
    sessions: tuple[AgentSession | None, ...],
    *,
    page: int,
    has_next: bool,
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
                    color="control",
                    selected=False,
                    enabled=True,
                    confidence="observed",
                    action="new_session",
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
                color=_display_color(session.state),
                selected=session.selected,
                enabled=True,
                confidence=session.confidence,
                session_id=session.id,
            )
        )

    first_control = (
        DeckButton(
            id="control:refresh",
            position=10,
            kind="control",
            label="Refresh",
            detail="Reorder by activity",
            icon="action",
            color="control",
            selected=False,
            enabled=True,
            confidence="observed",
            action="refresh_sessions",
        )
        if page == 1
        else DeckButton(
            id="control:previous",
            position=10,
            kind="control",
            label="Previous",
            detail=f"Page {page - 1}",
            icon="action",
            color="control",
            selected=False,
            enabled=True,
            confidence="observed",
            action="previous_page",
        )
    )
    empty_controls = tuple(
        DeckButton(
            id=f"empty:{position}",
            position=position,
            kind="empty",
            label="",
            detail="",
            icon="action",
            color="unknown",
            selected=False,
            enabled=False,
            confidence="unknown",
        )
        for position in range(11, 14)
    )
    last_control = (
        DeckButton(
            id="control:next",
            position=14,
            kind="control",
            label="Next",
            detail=f"Page {page + 1}",
            icon="action",
            color="control",
            selected=False,
            enabled=True,
            confidence="observed",
            action="next_page",
        )
        if has_next
        else DeckButton(
            id="control:new",
            position=14,
            kind="control",
            label="New",
            detail="Create Cursor agent",
            icon="plus",
            color="control",
            selected=False,
            enabled=True,
            confidence="observed",
            action="new_session",
        )
    )
    controls = (
        first_control,
        *empty_controls,
        last_control,
    )
    buttons.extend(controls)
    return buttons


def _session_detail(workspace_path: str | None) -> str:
    if workspace_path is None:
        return "Unknown workspace"
    name = Path(workspace_path).name
    return name or workspace_path


def _display_color(state: SessionState) -> ButtonColor:
    if state == "error":
        return "waiting"
    if state == "unknown":
        return "idle"
    return state
