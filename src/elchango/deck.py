"""Map provider sessions onto the fixed 3 by 5 v0.1 deck."""

from __future__ import annotations

import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable, Mapping

from elchango.models import (
    AgentSession,
    ButtonColor,
    DeckButton,
    DeckSnapshot,
    SessionState,
)
from elchango.providers.base import AgentProvider, ProviderError


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
    provider_picker_open: bool
    revision: int
    signature: object | None
    last_accessed: float


@dataclass(frozen=True, slots=True)
class _CombinedSnapshot:
    observed_at_ms: int
    selected_session_id: str | None
    sessions: tuple[AgentSession, ...]
    source: str
    read_only: bool


class DeckService:
    """Build stable, versioned render snapshots across provider adapters."""

    def __init__(
        self,
        providers: AgentProvider | Mapping[str, AgentProvider],
        *,
        default_provider_id: str | None = None,
        max_client_states: int = DEFAULT_MAX_CLIENT_STATES,
        client_state_ttl_seconds: float = DEFAULT_CLIENT_STATE_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_client_states < 1:
            raise ValueError("max_client_states must be positive")
        if client_state_ttl_seconds <= 0:
            raise ValueError("client_state_ttl_seconds must be positive")
        if isinstance(providers, Mapping):
            self._providers = dict(providers)
        else:
            self._providers = {providers.provider_id: providers}
        for provider_id, provider in self._providers.items():
            if provider_id != provider.provider_id:
                raise ValueError("provider registry key must match provider_id")
        self._default_provider_id = (
            default_provider_id or next(iter(self._providers), None)
        )
        if (
            self._default_provider_id is not None
            and self._default_provider_id not in self._providers
        ):
            raise ValueError("default_provider_id is not registered")
        self._lock = threading.Lock()
        self._provider_error_lock = threading.Lock()
        self._provider_errors: dict[str, str] = {}
        self._session_order: list[str | None] = []
        self._refresh_requested = True
        self._max_client_states = max_client_states
        self._client_state_ttl_seconds = client_state_ttl_seconds
        self._clock = clock
        self._client_states: OrderedDict[str, _ClientState] = OrderedDict()

    def snapshot(self, client_id: str = DEFAULT_CLIENT_ID) -> DeckSnapshot:
        client_id = validate_client_id(client_id)
        provider_snapshot = self._combined_snapshot()
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
                state.provider_picker_open,
                tuple(
                    (
                        provider.provider_id,
                        provider.display_name,
                        provider.icon,
                    )
                    for provider in self._new_session_providers()
                ),
                provider_snapshot.source,
                provider_snapshot.read_only,
            )
            if signature != state.signature:
                state.revision += 1
                state.signature = signature
            revision = state.revision
            provider_picker_open = state.provider_picker_open

        if provider_picker_open:
            buttons = _build_provider_picker_buttons(
                self._new_session_providers()
            )
            page = 1
            page_count = 1
            has_previous = False
            has_next = False
        else:
            buttons = _build_buttons(
                visible_sessions,
                page=page,
                has_next=has_next,
                launch_enabled=bool(self._new_session_providers()),
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

    def _combined_snapshot(self) -> _CombinedSnapshot:
        snapshots = []
        provider_errors: dict[str, str] = {}
        for provider_id, provider in self._providers.items():
            try:
                snapshots.append(provider.snapshot())
            except ProviderError as error:
                provider_errors[provider_id] = str(error)
        with self._provider_error_lock:
            self._provider_errors = provider_errors
        if not snapshots:
            source = (
                ";".join(
                    f"{provider_id}=unavailable: {error}"
                    for provider_id, error in provider_errors.items()
                )
                or "no providers available"
            )
            return _CombinedSnapshot(
                observed_at_ms=time.time_ns() // 1_000_000,
                selected_session_id=None,
                sessions=(),
                source=source,
                read_only=True,
            )
        selected_session_id = next(
            (
                snapshot.selected_session_id
                for snapshot in snapshots
                if snapshot.selected_session_id is not None
            ),
            None,
        )
        return _CombinedSnapshot(
            observed_at_ms=max(snapshot.observed_at_ms for snapshot in snapshots),
            selected_session_id=selected_session_id,
            sessions=tuple(
                session
                for snapshot in snapshots
                for session in snapshot.sessions
            ),
            source=";".join(
                (
                    *(
                        f"{snapshot.provider_id}={snapshot.source}"
                        for snapshot in snapshots
                    ),
                    *(
                        f"{provider_id}=unavailable: {error}"
                        for provider_id, error in provider_errors.items()
                    ),
                )
            ),
            read_only=all(snapshot.read_only for snapshot in snapshots),
        )

    def provider_errors(self) -> dict[str, str]:
        """Return provider failures observed during the latest snapshot."""

        with self._provider_error_lock:
            return dict(self._provider_errors)

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

    def choose_new_provider(
        self,
        client_id: str = DEFAULT_CLIENT_ID,
    ) -> DeckSnapshot:
        """Replace one client's session deck with the provider chooser."""

        client_id = validate_client_id(client_id)
        providers = self._new_session_providers()
        if not providers:
            raise ValueError("no provider supports new sessions")
        provider_positions(len(providers))
        with self._lock:
            self._client_state(client_id).provider_picker_open = True
        return self.snapshot(client_id)

    def cancel_new_session(
        self,
        client_id: str = DEFAULT_CLIENT_ID,
    ) -> DeckSnapshot:
        """Return one client from the provider chooser to its session page."""

        client_id = validate_client_id(client_id)
        with self._lock:
            state = self._client_state(client_id)
            if not state.provider_picker_open:
                raise ValueError("new session provider chooser is not open")
            state.provider_picker_open = False
        return self.snapshot(client_id)

    def complete_new_session(
        self,
        client_id: str = DEFAULT_CLIENT_ID,
    ) -> DeckSnapshot:
        """Close the provider chooser after an accepted native launch."""

        return self.cancel_new_session(client_id)

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
                provider_picker_open=False,
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
        snapshot: _CombinedSnapshot,
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

    def _new_session_providers(self) -> tuple[AgentProvider, ...]:
        return tuple(
            provider
            for provider in self._providers.values()
            if "new_session" in provider.capabilities
        )


def _build_buttons(
    sessions: tuple[AgentSession | None, ...],
    *,
    page: int,
    has_next: bool,
    launch_enabled: bool,
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
                    enabled=launch_enabled,
                    confidence="observed",
                    action="choose_new_provider",
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
                icon=session.icon,
                color=_display_color(session.state),
                selected=session.selected,
                enabled="focus_session" in session.capabilities,
                confidence=session.confidence,
                session_id=session.id,
                provider_id=session.provider_id,
                native_session_id=session.native_id,
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
            detail="Create agent",
            icon="plus",
            color="control",
            selected=False,
            enabled=launch_enabled,
            confidence="observed",
            action="choose_new_provider",
        )
    )
    controls = (
        first_control,
        *empty_controls,
        last_control,
    )
    buttons.extend(controls)
    return buttons


def provider_positions(count: int) -> tuple[int, ...]:
    """Return centered row-two positions for one through five providers."""

    positions = {
        1: (7,),
        2: (6, 8),
        3: (6, 7, 8),
        4: (5, 6, 8, 9),
        5: (5, 6, 7, 8, 9),
    }
    try:
        return positions[count]
    except KeyError as error:
        raise ValueError("provider chooser supports between one and five providers") from error


def _build_provider_picker_buttons(
    providers: tuple[AgentProvider, ...],
) -> list[DeckButton]:
    positions = provider_positions(len(providers))
    buttons = [
        _blank_button(position)
        for position in range(TOTAL_BUTTONS)
    ]
    for position, provider in zip(positions, providers, strict=True):
        buttons[position] = DeckButton(
            id=f"provider:{provider.provider_id}",
            position=position,
            kind="control",
            label=provider.display_name,
            detail="Create agent",
            icon=provider.icon,
            color="control",
            selected=False,
            enabled=True,
            confidence="observed",
            action="new_session",
            provider_id=provider.provider_id,
        )
    buttons[10] = DeckButton(
        id="control:cancel-new",
        position=10,
        kind="control",
        label="Cancel",
        detail="Return to sessions",
        icon="arrow-left",
        color="control",
        selected=False,
        enabled=True,
        confidence="observed",
        action="cancel_new_session",
    )
    return buttons


def _blank_button(position: int) -> DeckButton:
    return DeckButton(
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


def _display_color(state: SessionState) -> ButtonColor:
    if state == "error":
        return "waiting"
    if state == "unknown":
        return "idle"
    return state
