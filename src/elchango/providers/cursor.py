"""Read-only Cursor provider backed by the native local state database."""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from elchango.models import (
    AgentSession,
    ProviderSnapshot,
    SessionState,
    StateConfidence,
)


DEFAULT_CURSOR_ROOT = Path.home() / "Library/Application Support/Cursor/User"
DEFAULT_DATABASE = DEFAULT_CURSOR_ROOT / "globalStorage/state.vscdb"
DEFAULT_WORKSPACE_STORAGE = DEFAULT_CURSOR_ROOT / "workspaceStorage"
SELECTED_AGENT_KEY = "cursor/glass.selectedAgent"
MEMBERSHIP_KEY = "glass.localAgentProjectMembership.v1"
REQUIRED_TABLES = {"ItemTable", "composerHeaders", "cursorDiskKV"}


class CursorProviderError(RuntimeError):
    """Cursor data could not be read conservatively."""


@dataclass(frozen=True, slots=True)
class CursorProvider:
    """Normalize native local Cursor sessions without modifying Cursor state."""

    database: Path = DEFAULT_DATABASE
    workspace_storage: Path = DEFAULT_WORKSPACE_STORAGE

    def snapshot(self) -> ProviderSnapshot:
        connection = self._connect()
        try:
            self._validate_schema(connection)
            selected_id = self._read_selected_id(connection)
            workspace_paths = self._load_workspace_paths()
            memberships = self._read_item_object(connection, MEMBERSHIP_KEY)
            sessions = self._read_sessions(
                connection,
                selected_id,
                workspace_paths,
                memberships,
            )
        except sqlite3.Error as error:
            raise CursorProviderError(f"Cursor database read failed: {error}") from error
        finally:
            connection.close()

        return ProviderSnapshot(
            observed_at_ms=time.time_ns() // 1_000_000,
            selected_session_id=selected_id,
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
    ) -> list[AgentSession]:
        sessions: list[AgentSession] = []
        rows = connection.execute(
            """
            SELECT composerId, workspaceId, lastUpdatedAt, recency,
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
            updated_at_ms = _integer_or_none(row["recency"]) or _integer_or_none(
                row["lastUpdatedAt"]
            )
            if updated_at_ms is None:
                continue

            workspace_id = str(row["workspaceId"] or "")
            state, confidence, detail = self._infer_state(connection, composer_id)
            sessions.append(
                AgentSession(
                    id=composer_id,
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
                    updated_at_ms=updated_at_ms,
                )
            )

        sessions.sort(key=lambda session: session.updated_at_ms, reverse=True)
        return sessions

    def _infer_state(
        self,
        connection: sqlite3.Connection,
        composer_id: str,
    ) -> tuple[SessionState, StateConfidence, str]:
        data = self._read_disk_object(connection, f"composerData:{composer_id}")
        if not data:
            return "unknown", "unknown", "composer data unavailable"

        tool_status, result_status = self._latest_tool_status(
            connection,
            composer_id,
            data,
        )
        tool = tool_status.lower() if tool_status else None
        result = result_status.lower() if result_status else None

        if result in {"error", "failed", "failure"}:
            return "error", "observed", f"tool result {result}"
        if tool in {"error", "failed", "failure"}:
            return "error", "observed", f"tool {tool}"
        if bool(data.get("hasBlockingPendingActions", False)):
            return "waiting", "candidate", "blocking action pending"
        if result in {"aborted", "cancelled", "canceled"}:
            return "unknown", "candidate", f"provisional tool result {result}"
        if tool in {"loading", "running", "pending", "in_progress"}:
            return "working", "candidate", f"tool {tool}"
        if data.get("generatingBubbleIds") or bool(
            data.get("isContinuationInProgress", False)
        ):
            return "working", "candidate", "generation signal present"
        if tool == "completed":
            detail = (
                "tool completed successfully"
                if result == "success"
                else "tool completed"
            )
            return "done", "persisted", detail

        raw_status = _string_or_none(data.get("status"))
        if raw_status in {"error", "failed"}:
            return "error", "persisted", f"composer {raw_status}"
        if raw_status == "completed":
            return "done", "persisted", "last turn completed"
        if raw_status == "aborted":
            return "idle", "persisted", "last turn aborted"
        if raw_status in {"generating", "running", "pending"}:
            return "working", "candidate", f"composer {raw_status}"
        return "unknown", "unknown", "no decisive state signal"

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
