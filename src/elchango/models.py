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
ProviderCapability = Literal["focus_session", "new_session"]
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

    provider_id: str
    native_id: str
    capabilities: frozenset[ProviderCapability]
    icon: ButtonIcon
    title: str
    workspace_id: str
    workspace_path: str | None
    state: SessionState
    confidence: StateConfidence
    state_detail: str
    selected: bool
    last_activity_at_ms: int

    @property
    def id(self) -> str:
        """Return the opaque provider-qualified public session identifier."""

        return qualify_session_id(self.provider_id, self.native_id)


@dataclass(frozen=True, slots=True)
class ProviderSnapshot:
    """One atomic read from an agent provider."""

    provider_id: str
    capabilities: frozenset[ProviderCapability]
    observed_at_ms: int
    selected_native_session_id: str | None
    sessions: tuple[AgentSession, ...]
    source: str
    read_only: bool = True

    @property
    def selected_session_id(self) -> str | None:
        """Return the selected session as an opaque qualified identifier."""

        if self.selected_native_session_id is None:
            return None
        return qualify_session_id(
            self.provider_id,
            self.selected_native_session_id,
        )


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
    provider_id: str | None = None
    native_session_id: str | None = None

    def to_dict(self) -> dict[str, object]:
        """Return the public button contract without native provider IDs."""

        payload = asdict(self)
        payload.pop("native_session_id")
        return payload


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

        return {
            "revision": self.revision,
            "observed_at_ms": self.observed_at_ms,
            "source": self.source,
            "read_only": self.read_only,
            "selected_session_id": self.selected_session_id,
            "page": self.page,
            "page_count": self.page_count,
            "has_previous": self.has_previous,
            "has_next": self.has_next,
            "buttons": [button.to_dict() for button in self.buttons],
        }


def qualify_session_id(provider_id: str, native_id: str) -> str:
    """Build an opaque public ID while retaining structured identity internally."""

    if not provider_id or ":" in provider_id:
        raise ValueError("provider_id must be non-empty and cannot contain ':'")
    if not native_id:
        raise ValueError("native session ID must be non-empty")
    return f"{provider_id}:{native_id}"
