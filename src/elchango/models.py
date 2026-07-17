"""Provider and deck models shared by the local service."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal


SessionState = Literal[
    "idle",
    "working",
    "waiting",
    "done",
    "error",
    "unknown",
]
StateConfidence = Literal["observed", "candidate", "persisted", "unknown"]
ButtonKind = Literal["session", "control", "empty"]
ButtonIcon = Literal[
    "cursor",
    "plus",
    "arrow-left",
    "arrow-right",
    "arrows-clockwise",
]
ButtonColor = Literal[
    "idle",
    "working",
    "waiting",
    "done",
    "error",
    "unknown",
    "control",
]
DeckAction = Literal[
    "new_session",
    "refresh_sessions",
    "previous_page",
    "next_page",
    "primary_action",
    "secondary_action",
    "stop_session",
]


@dataclass(frozen=True, slots=True)
class AgentSession:
    """Normalized session data that the deck is allowed to consume."""

    id: str
    title: str
    workspace_id: str
    workspace_path: str | None
    state: SessionState
    confidence: StateConfidence
    state_detail: str
    selected: bool
    last_activity_at_ms: int


@dataclass(frozen=True, slots=True)
class ProviderSnapshot:
    """One atomic read from an agent provider."""

    observed_at_ms: int
    selected_session_id: str | None
    sessions: tuple[AgentSession, ...]
    source: str
    read_only: bool = True


@dataclass(frozen=True, slots=True)
class DeckButton:
    """Surface-neutral description of one physical-style deck key."""

    id: str
    position: int
    kind: ButtonKind
    label: str
    detail: str
    icon: ButtonIcon
    color: ButtonColor
    selected: bool
    enabled: bool
    confidence: StateConfidence
    session_id: str | None = None
    action: DeckAction | None = None


@dataclass(frozen=True, slots=True)
class DeckSnapshot:
    """Versioned render model returned to the web deck."""

    revision: int
    observed_at_ms: int
    source: str
    read_only: bool
    selected_session_id: str | None
    page: int
    page_count: int
    has_previous: bool
    has_next: bool
    buttons: tuple[DeckButton, ...]

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready snapshot."""

        return asdict(self)
