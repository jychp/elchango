"""Persist user-owned deck personalization outside provider state."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from elchango.models import ButtonIcon, CommandId


PREFERENCES_VERSION = 1
DEFAULT_PREFERENCES_PATH = (
    Path.home() / "Library/Application Support/elChango/preferences.json"
)
DEFAULT_ACTION_SLOTS: tuple[CommandId, CommandId, CommandId] = (
    "accept",
    "commit_push",
    "create_pr",
)


@dataclass(frozen=True, slots=True)
class DeckPreferences:
    """Validated personalization shared by all deck clients."""

    session_icons: dict[str, ButtonIcon]
    action_slots: tuple[CommandId, CommandId, CommandId]


class PreferencesStore:
    """Load and atomically update versioned deck preferences."""

    def __init__(self, path: Path = DEFAULT_PREFERENCES_PATH) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._preferences = self._load()

    def snapshot(self) -> DeckPreferences:
        with self._lock:
            return DeckPreferences(
                session_icons=dict(self._preferences.session_icons),
                action_slots=self._preferences.action_slots,
            )

    def set_session_icon(self, session_id: str, icon: ButtonIcon) -> None:
        with self._lock:
            icons = dict(self._preferences.session_icons)
            icons[session_id] = icon
            self._replace(DeckPreferences(icons, self._preferences.action_slots))

    def set_action_slot(self, slot: int, command_id: CommandId) -> None:
        if slot not in range(3):
            raise ValueError("action slot must be between 0 and 2")
        with self._lock:
            slots = list(self._preferences.action_slots)
            slots[slot] = command_id
            self._replace(
                DeckPreferences(
                    dict(self._preferences.session_icons),
                    tuple(slots),  # type: ignore[arg-type]
                )
            )

    def _load(self) -> DeckPreferences:
        if not self.path.exists():
            return DeckPreferences({}, DEFAULT_ACTION_SLOTS)
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError(f"{self.path}: invalid preferences: {error}") from error
        if not isinstance(payload, dict) or payload.get("version") != PREFERENCES_VERSION:
            raise ValueError(f"{self.path}: unsupported preferences schema")
        icons = payload.get("session_icons")
        slots = payload.get("action_slots")
        from elchango.models import ICON_OPTIONS, COMMAND_IDS

        if not isinstance(icons, dict) or any(
            not isinstance(session_id, str)
            or not session_id
            or icon not in ICON_OPTIONS
            for session_id, icon in icons.items()
        ):
            raise ValueError(f"{self.path}: invalid session icon preferences")
        if (
            not isinstance(slots, list)
            or len(slots) != 3
            or any(command_id not in COMMAND_IDS for command_id in slots)
        ):
            raise ValueError(f"{self.path}: invalid action slot preferences")
        return DeckPreferences(
            session_icons=dict(icons),
            action_slots=tuple(slots),  # type: ignore[arg-type]
        )

    def _replace(self, preferences: DeckPreferences) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": PREFERENCES_VERSION,
            "session_icons": preferences.session_icons,
            "action_slots": list(preferences.action_slots),
        }
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            dir=self.path.parent,
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, indent=2, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, self.path)
        finally:
            temporary_path.unlink(missing_ok=True)
        self._preferences = preferences
