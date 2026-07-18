"""Read-only Cursor provider backed by the native local state database."""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, ClassVar
from urllib.parse import unquote, urlparse

from elchango.activity import ActivityStore
from elchango.models import (
    AgentSession,
    ProviderCapability,
    ProviderSnapshot,
    SessionState,
    StateConfidence,
)
from elchango.providers.base import ProviderError


DEFAULT_CURSOR_ROOT = Path.home() / "Library/Application Support/Cursor/User"
DEFAULT_DATABASE = DEFAULT_CURSOR_ROOT / "globalStorage/state.vscdb"
DEFAULT_WORKSPACE_STORAGE = DEFAULT_CURSOR_ROOT / "workspaceStorage"
SELECTED_AGENT_KEY = "cursor/glass.selectedAgent"
MEMBERSHIP_KEY = "glass.localAgentProjectMembership.v1"
REQUIRED_TABLES = {"ItemTable", "composerHeaders", "cursorDiskKV"}
ACTIVE_SIGNAL_TTL_MS = 5 * 60 * 1_000


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


class CursorProviderError(ProviderError):
    """Cursor data could not be read conservatively."""


@dataclass(frozen=True, slots=True)
class CursorProvider:
    """Normalize native local Cursor sessions without modifying Cursor state."""

    provider_id: ClassVar[str] = "cursor"
    capabilities: ClassVar[frozenset[ProviderCapability]] = frozenset(
        {"focus_session", "new_session"}
    )
    database: Path = DEFAULT_DATABASE
    workspace_storage: Path = DEFAULT_WORKSPACE_STORAGE
    active_signal_ttl_ms: int = ACTIVE_SIGNAL_TTL_MS
    clock: Callable[[], int] = _now_ms
    activity_store: ActivityStore | None = None

    def snapshot(self) -> ProviderSnapshot:
        observed_at_ms = self.clock()
        connection = self._connect()
        try:
            self._validate_schema(connection)
            selected_id = self._read_selected_id(connection)
            if self.activity_store is not None:
                self.activity_store.observe_selection(
                    selected_id,
                    observed_at_ms,
                )
            workspace_paths = self._load_workspace_paths()
            memberships = self._read_item_object(connection, MEMBERSHIP_KEY)
            sessions = self._read_sessions(
                connection,
                selected_id,
                workspace_paths,
                memberships,
                observed_at_ms,
            )
        except sqlite3.Error as error:
            raise CursorProviderError(f"Cursor database read failed: {error}") from error
        finally:
            connection.close()

        return ProviderSnapshot(
            provider_id=self.provider_id,
            capabilities=self.capabilities,
            observed_at_ms=observed_at_ms,
            selected_native_session_id=selected_id,
            sessions=tuple(sessions),
            source=str(self.database),
            read_only=True,
        )

    def _connect(self) -> sqlite3.Connection:
        if not self.database.is_file():
            raise CursorProviderError(f"Cursor database not found: {self.database}")
        connection = sqlite3.connect(
            f"{self.database.resolve().as_uri()}?mode=ro",
            uri=True,
            timeout=2,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        return connection

    @staticmethod
    def _validate_schema(connection: sqlite3.Connection) -> None:
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        missing = REQUIRED_TABLES - tables
        if missing:
            raise CursorProviderError(
                f"Unsupported Cursor schema; missing tables: {sorted(missing)}"
            )

    def _read_sessions(
        self,
        connection: sqlite3.Connection,
        selected_id: str | None,
        workspace_paths: dict[str, str],
        memberships: dict[str, Any],
        observed_at_ms: int,
    ) -> list[AgentSession]:
        sessions: list[AgentSession] = []
        rows = connection.execute(
            """
            SELECT composerId, workspaceId, lastUpdatedAt,
                   isArchived, isSubagent, value
            FROM composerHeaders
            """
        )
        for row in rows:
            composer_id = str(row["composerId"])
            if memberships and composer_id not in memberships:
                continue
            header = _parse_object(row["value"])
            if (
                bool(row["isArchived"])
                or bool(row["isSubagent"])
                or bool(header.get("isDraft", False))
                or bool(header.get("isEphemeral", False))
            ):
                continue
            last_activity_at_ms = _integer_or_none(row["lastUpdatedAt"])
            if last_activity_at_ms is None:
                continue

            workspace_id = str(row["workspaceId"] or "")
            data = self._read_disk_object(
                connection,
                f"composerData:{composer_id}",
            )
            state, confidence, detail = self._infer_state(
                connection,
                composer_id,
                header,
                data,
                last_activity_at_ms,
                observed_at_ms,
            )
            if self.activity_store is not None:
                hook_state = self.activity_store.state_for(
                    composer_id,
                    observed_at_ms,
                    _string_or_none(
                        data.get("latestChatGenerationUUID")
                        or data.get("chatGenerationUUID")
                    ),
                )
                if hook_state is not None:
                    hook_session_state, hook_confidence, hook_detail = hook_state
                    if hook_session_state in {"done", "error"} or state != "waiting":
                        state = hook_session_state
                        confidence = hook_confidence
                        detail = hook_detail
            sessions.append(
                AgentSession(
                    provider_id=self.provider_id,
                    native_id=composer_id,
                    capabilities=self.capabilities,
                    icon="cursor",
                    title=_string_or_none(header.get("name")) or "Untitled session",
                    workspace_id=workspace_id,
                    workspace_path=(
                        _embedded_workspace_path(header)
                        or workspace_paths.get(workspace_id)
                    ),
                    state=state,
                    confidence=confidence,
                    state_detail=detail,
                    selected=composer_id == selected_id,
                    last_activity_at_ms=last_activity_at_ms,
                )
            )

        sessions.sort(
            key=lambda session: (-session.last_activity_at_ms, session.id)
        )
        return sessions

    def _infer_state(
        self,
        connection: sqlite3.Connection,
        composer_id: str,
        header: dict[str, Any],
        data: dict[str, Any],
        last_activity_at_ms: int,
        observed_at_ms: int,
    ) -> tuple[SessionState, StateConfidence, str]:
        if not data:
            return "idle", "unknown", "composer data unavailable"

        tool_status, result_status = self._latest_tool_status(
            connection,
            composer_id,
            data,
        )
        tool = tool_status.lower() if tool_status else None
        result = result_status.lower() if result_status else None
        active_signal_is_fresh = (
            observed_at_ms - last_activity_at_ms <= self.active_signal_ttl_ms
        )

        blocking = bool(
            data.get("hasBlockingPendingActions", False)
            or header.get("hasBlockingPendingActions", False)
            or data.get("hasPendingPlan", False)
            or header.get("hasPendingPlan", False)
        )
        if blocking:
            if active_signal_is_fresh:
                return "waiting", "candidate", "user action or plan pending"
            return "idle", "persisted", "stale blocking signal ignored"
        if result in {
            "error",
            "failed",
            "failure",
            "aborted",
            "cancelled",
            "canceled",
        } or tool in {"error", "failed", "failure"}:
            if active_signal_is_fresh:
                return (
                    "working",
                    "candidate",
                    "recent provisional tool result; turn may continue",
                )
            return "idle", "persisted", "stale tool result ignored"
        if tool in {"loading", "running", "pending", "in_progress"}:
            if active_signal_is_fresh:
                return "working", "candidate", f"tool {tool}"
            return "idle", "persisted", f"stale tool {tool} signal ignored"
        if data.get("generatingBubbleIds") or bool(
            data.get("isContinuationInProgress", False)
        ):
            if active_signal_is_fresh:
                return "working", "candidate", "generation signal present"
            return "idle", "persisted", "stale generation signal ignored"
        if tool == "completed":
            if active_signal_is_fresh:
                return (
                    "working",
                    "candidate",
                    "recent tool completion; awaiting terminal signal",
                )
            detail = (
                "last tool completed successfully"
                if result == "success"
                else "last tool completed"
            )
            return "idle", "persisted", detail

        raw_status = _string_or_none(data.get("status"))
        if raw_status in {"error", "failed"}:
            if active_signal_is_fresh:
                return "waiting", "candidate", f"composer {raw_status}"
            return "idle", "persisted", f"stale composer {raw_status}"
        if raw_status == "completed":
            if active_signal_is_fresh:
                return (
                    "working",
                    "candidate",
                    "recent completion; awaiting terminal signal",
                )
            return "idle", "persisted", "last turn completed"
        if raw_status == "aborted":
            if active_signal_is_fresh:
                return (
                    "working",
                    "candidate",
                    "recent activity with stale aborted status",
                )
            return "idle", "persisted", "last turn aborted"
        if raw_status in {"generating", "running", "pending"}:
            if active_signal_is_fresh:
                return "working", "candidate", f"composer {raw_status}"
            return "idle", "persisted", f"stale composer {raw_status} ignored"
        if active_signal_is_fresh:
            return "working", "candidate", "recent composer activity"
        return "idle", "persisted", "no active state signal"

    def _latest_tool_status(
        self,
        connection: sqlite3.Connection,
        composer_id: str,
        data: dict[str, Any],
    ) -> tuple[str | None, str | None]:
        headers = data.get("fullConversationHeadersOnly")
        if not isinstance(headers, list):
            return None, None
        for header in reversed(headers[-20:]):
            if not isinstance(header, dict):
                continue
            bubble_id = _string_or_none(header.get("bubbleId"))
            if bubble_id is None:
                continue
            bubble = self._read_disk_object(
                connection,
                f"bubbleId:{composer_id}:{bubble_id}",
            )
            tool_data = bubble.get("toolFormerData")
            if not isinstance(tool_data, dict):
                continue
            additional = tool_data.get("additionalData")
            result_status = (
                additional.get("status") if isinstance(additional, dict) else None
            )
            return (
                _string_or_none(tool_data.get("status")),
                _string_or_none(result_status),
            )
        return None, None

    @staticmethod
    def _read_disk_object(
        connection: sqlite3.Connection,
        key: str,
    ) -> dict[str, Any]:
        row = connection.execute(
            "SELECT value FROM cursorDiskKV WHERE key = ?",
            (key,),
        ).fetchone()
        return _parse_object(row["value"]) if row is not None else {}

    @staticmethod
    def _read_item_object(
        connection: sqlite3.Connection,
        key: str,
    ) -> dict[str, Any]:
        row = connection.execute(
            "SELECT value FROM ItemTable WHERE key = ?",
            (key,),
        ).fetchone()
        return _parse_object(row["value"]) if row is not None else {}

    @staticmethod
    def _read_selected_id(connection: sqlite3.Connection) -> str | None:
        row = connection.execute(
            "SELECT value FROM ItemTable WHERE key = ?",
            (SELECTED_AGENT_KEY,),
        ).fetchone()
        if row is None:
            return None
        value = _decode_text(row["value"])
        return value.strip() if value and value.strip() else None

    def _load_workspace_paths(self) -> dict[str, str]:
        paths: dict[str, str] = {}
        if not self.workspace_storage.is_dir():
            return paths
        for workspace_file in self.workspace_storage.glob("*/workspace.json"):
            try:
                payload = json.loads(workspace_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            folder = payload.get("folder")
            if isinstance(folder, str):
                path = _file_uri_to_path(folder)
                if path:
                    paths[workspace_file.parent.name] = path
        return paths


def _decode_text(value: Any) -> str | None:
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return None
    return value if isinstance(value, str) else None


def _parse_object(value: Any) -> dict[str, Any]:
    text = _decode_text(value)
    if text is None:
        return {}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _integer_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) else None


def _file_uri_to_path(uri: str) -> str | None:
    parsed = urlparse(uri)
    return unquote(parsed.path) if parsed.scheme == "file" else None


def _embedded_workspace_path(header: dict[str, Any]) -> str | None:
    agent_location = header.get("agentLocation")
    environment = (
        agent_location.get("environment")
        if isinstance(agent_location, dict)
        else None
    )
    workspace_identifier = header.get("workspaceIdentifier")
    candidates = [
        environment.get("uri") if isinstance(environment, dict) else None,
        (
            workspace_identifier.get("uri")
            if isinstance(workspace_identifier, dict)
            else None
        ),
    ]
    for uri in candidates:
        if not isinstance(uri, dict):
            continue
        path = uri.get("fsPath") or uri.get("path")
        if isinstance(path, str) and path:
            return path
        external = uri.get("external")
        if isinstance(external, str):
            path = _file_uri_to_path(external)
            if path:
                return path
    return None
