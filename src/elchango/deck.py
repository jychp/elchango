"""Map provider sessions onto the fixed 3 by 5 v0.1 deck."""

from __future__ import annotations

import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable

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
DEFAULT_CLIENT_ID = "web"
MAX_CLIENT_ID_LENGTH = 128
DEFAULT_MAX_CLIENT_STATES = 64
DEFAULT_CLIENT_STATE_TTL_SECONDS = 30 * 60
_CLIENT_ID_PATTERN = re.compile(r"[A-Za-z0-9._:-]+")


def validate_client_id(client_id: str) -> str:
    """Validate and return a conservative deck client identifier."""

    if (
        not isinstance(client_id, str)
        or not client_id
        or len(client_id) > MAX_CLIENT_ID_LENGTH
        or _CLIENT_ID_PATTERN.fullmatch(client_id) is None
    ):
        raise ValueError(
            "client_id must be 1-128 ASCII characters using only "
            "letters, numbers, '.', '_', ':', or '-'"
        )
    return client_id


@dataclass(slots=True)
class _ClientState:
    page_index: int
    revision: int
    signature: object | None
    last_accessed: float


class DeckService:
    """Build stable, versioned render snapshots from one provider."""

    def __init__(
        self,
        provider: AgentProvider,
        *,
        max_client_states: int = DEFAULT_MAX_CLIENT_STATES,
        client_state_ttl_seconds: float = DEFAULT_CLIENT_STATE_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_client_states < 1:
            raise ValueError("max_client_states must be positive")
        if client_state_ttl_seconds <= 0:
            raise ValueError("client_state_ttl_seconds must be positive")
        self._provider = provider
        self._lock = threading.Lock()
        self._session_order: list[str | None] = []
        self._refresh_requested = True
        self._max_client_states = max_client_states
        self._client_state_ttl_seconds = client_state_ttl_seconds
        self._clock = clock
        self._client_states: OrderedDict[str, _ClientState] = OrderedDict()

    def snapshot(self, client_id: str = DEFAULT_CLIENT_ID) -> DeckSnapshot:
        client_id = validate_client_id(client_id)
        provider_snapshot = self._provider.snapshot()
        with self._lock:
            state = self._client_state(client_id)
            sessions_by_id = self._update_session_order(provider_snapshot)
            page_count = self._page_count()
            state.page_index = min(state.page_index, page_count - 1)
            visible_sessions = self._visible_sessions(
                sessions_by_id,
                state.page_index,
            )
            has_previous = state.page_index > 0
            has_next = state.page_index + 1 < page_count
            page = state.page_index + 1
            signature = (
                provider_snapshot.selected_session_id,
                provider_snapshot.sessions,
                tuple(self._session_order),
                state.page_index,
                provider_snapshot.source,
                provider_snapshot.read_only,
            )
            if signature != state.signature:
                state.revision += 1
                state.signature = signature
            revision = state.revision

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

    def refresh(self, client_id: str = DEFAULT_CLIENT_ID) -> DeckSnapshot:
        client_id = validate_client_id(client_id)
        with self._lock:
            self._refresh_requested = True
            self._client_state(client_id).page_index = 0
        return self.snapshot(client_id)

    def previous_page(self, client_id: str = DEFAULT_CLIENT_ID) -> DeckSnapshot:
        client_id = validate_client_id(client_id)
        with self._lock:
            state = self._client_state(client_id)
            if state.page_index <= 0:
                raise ValueError("the deck is already on the first page")
            state.page_index -= 1
        return self.snapshot(client_id)

    def next_page(self, client_id: str = DEFAULT_CLIENT_ID) -> DeckSnapshot:
        client_id = validate_client_id(client_id)
        with self._lock:
            state = self._client_state(client_id)
            if state.page_index + 1 >= self._page_count():
                raise ValueError("the deck is already on the last page")
            state.page_index += 1
        return self.snapshot(client_id)

    @property
    def active_client_count(self) -> int:
        """Return the number of unexpired client render states."""

        with self._lock:
            self._expire_client_states(self._clock())
            return len(self._client_states)

    def _client_state(self, client_id: str) -> _ClientState:
        now = self._clock()
        self._expire_client_states(now)
        state = self._client_states.get(client_id)
        if state is None:
            if len(self._client_states) >= self._max_client_states:
                self._client_states.popitem(last=False)
            state = _ClientState(
                page_index=0,
                revision=0,
                signature=None,
                last_accessed=now,
            )
            self._client_states[client_id] = state
        else:
            state.last_accessed = now
            self._client_states.move_to_end(client_id)
        return state

    def _expire_client_states(self, now: float) -> None:
        cutoff = now - self._client_state_ttl_seconds
        expired = [
            client_id
            for client_id, state in self._client_states.items()
            if state.last_accessed <= cutoff
        ]
        for client_id in expired:
            del self._client_states[client_id]

    def _update_session_order(
        self,
        snapshot: ProviderSnapshot,
    ) -> dict[str, AgentSession]:
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
        return sessions_by_id

    def _visible_sessions(
        self,
        sessions_by_id: dict[str, AgentSession],
        page_index: int,
    ) -> tuple[AgentSession | None, ...]:
        start = page_index * SESSION_SLOTS
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
                detail="",
                icon="cursor",
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
            icon="arrows-clockwise",
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
            icon="arrow-left",
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
            icon="arrows-clockwise",
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
            icon="arrow-right",
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


def _display_color(state: SessionState) -> ButtonColor:
    if state == "error":
        return "waiting"
    if state == "unknown":
        return "idle"
    return state
