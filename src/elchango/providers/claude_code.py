"""Read-only Claude Desktop Code inventory backed by persistent local records."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from elchango.claude_activity import ClaudeActivityStore
from elchango.models import (
    AgentSession,
    ProviderCapability,
    ProviderSnapshot,
)
from elchango.providers.base import ProviderActionResult, ProviderError


DEFAULT_DESKTOP_SESSIONS_ROOT = (
    Path.home() / "Library/Application Support/Claude/claude-code-sessions"
)
DEFAULT_CLAUDE_PROJECTS = Path.home() / ".claude/projects"
MAX_METADATA_PREFIX_BYTES = 64 * 1024
REQUIRED_FIELDS = {
    "sessionId",
    "cliSessionId",
    "cwd",
    "originCwd",
    "createdAt",
    "lastActivityAt",
    "isArchived",
}
OPTIONAL_FIELDS = {"title"}


class ClaudeCodeProviderError(ProviderError):
    """Claude Desktop local state is unavailable or has changed schema."""


@dataclass(frozen=True, slots=True)
class _DesktopRecord:
    desktop_session_id: str
    cli_session_id: str
    cwd: str
    origin_cwd: str
    created_at_ms: int
    last_activity_at_ms: int
    archived: bool
    title: str | None


@dataclass(frozen=True, slots=True)
class _CachedRecord:
    modified_at_ns: int
    size: int
    record: _DesktopRecord


class ClaudeCodeProvider:
    """Expose persistent Claude Desktop Code sessions without native actions."""

    provider_id: ClassVar[str] = "claude-code"
    capabilities: ClassVar[frozenset[ProviderCapability]] = frozenset()

    def __init__(
        self,
        desktop_sessions_root: Path = DEFAULT_DESKTOP_SESSIONS_ROOT,
        projects_root: Path = DEFAULT_CLAUDE_PROJECTS,
        activity_store: ClaudeActivityStore | None = None,
    ) -> None:
        self._desktop_sessions_root = desktop_sessions_root
        self._projects_root = projects_root
        self.activity_store = activity_store or ClaudeActivityStore()
        self._cache: dict[Path, _CachedRecord] = {}
        self._lock = threading.Lock()

    def snapshot(self) -> ProviderSnapshot:
        """Read persistent non-archived sessions and overlay fresh hook signals."""

        observed_at_ms = time.time_ns() // 1_000_000
        records = self._records()
        transcripts = self._transcripts_by_session_id()
        sessions: list[AgentSession] = []
        for record in records:
            if record.archived:
                continue
            state = self.activity_store.state_for(
                record.cli_session_id,
                observed_at_ms,
            )
            if state is None:
                session_state = "idle"
                confidence = "persisted"
                detail = "Persistent Claude Desktop session; no fresh hook signal"
            else:
                session_state, confidence, detail = state
            candidates = transcripts.get(record.cli_session_id, ())
            if len(candidates) != 1:
                raise ClaudeCodeProviderError(
                    "Claude Desktop session transcript mapping must be unique for "
                    f"{record.desktop_session_id}; found {len(candidates)}"
                )
            sessions.append(
                AgentSession(
                    provider_id=self.provider_id,
                    native_id=record.desktop_session_id,
                    capabilities=self.capabilities,
                    icon="claude",
                    title=record.title or "Untitled Claude session",
                    workspace_id=record.origin_cwd,
                    workspace_path=record.cwd,
                    state=session_state,
                    confidence=confidence,
                    state_detail=detail,
                    selected=False,
                    last_activity_at_ms=record.last_activity_at_ms,
                )
            )
        sessions.sort(key=lambda session: (-session.last_activity_at_ms, session.id))
        return ProviderSnapshot(
            provider_id=self.provider_id,
            capabilities=self.capabilities,
            observed_at_ms=observed_at_ms,
            selected_native_session_id=None,
            sessions=tuple(sessions),
            source=str(self._desktop_sessions_root),
        )

    def focus(self, native_session_id: str) -> ProviderActionResult:
        """Reject focus until Claude Desktop exposes exact post-action identity."""

        return ProviderActionResult(
            accepted=False,
            verdict="FOCUS_UNSUPPORTED",
            details={
                "session_id": native_session_id,
                "message": "Exact Claude Desktop session focus is not verified.",
            },
        )

    def open_new(self) -> ProviderActionResult:
        """Reject launch until provider selection and deep linking land in V3.4."""

        return ProviderActionResult(
            accepted=False,
            verdict="NEW_SESSION_UNSUPPORTED",
            details={
                "message": "Claude Desktop launch is deferred to V3.4.",
            },
        )

    def _records(self) -> tuple[_DesktopRecord, ...]:
        if not self._desktop_sessions_root.is_dir():
            raise ClaudeCodeProviderError(
                "Claude Desktop Code session directory not found: "
                f"{self._desktop_sessions_root}"
            )
        paths = tuple(
            sorted(self._desktop_sessions_root.glob("*/*/local_*.json"))
        )
        with self._lock:
            current_paths = set(paths)
            for removed in self._cache.keys() - current_paths:
                del self._cache[removed]
            records: list[_DesktopRecord] = []
            for path in paths:
                try:
                    status = path.stat()
                except OSError as error:
                    raise ClaudeCodeProviderError(
                        f"Cannot stat Claude Desktop session record {path}: {error}"
                    ) from error
                cached = self._cache.get(path)
                if (
                    cached is None
                    or cached.modified_at_ns != status.st_mtime_ns
                    or cached.size != status.st_size
                ):
                    record = _read_record(path)
                    cached = _CachedRecord(
                        modified_at_ns=status.st_mtime_ns,
                        size=status.st_size,
                        record=record,
                    )
                    self._cache[path] = cached
                records.append(cached.record)
        return tuple(records)

    def _transcripts_by_session_id(self) -> dict[str, tuple[Path, ...]]:
        if not self._projects_root.is_dir():
            raise ClaudeCodeProviderError(
                f"Claude Code projects directory not found: {self._projects_root}"
            )
        candidates: dict[str, list[Path]] = {}
        for path in self._projects_root.glob("*/*.jsonl"):
            candidates.setdefault(path.stem, []).append(path)
        return {
            session_id: tuple(sorted(paths))
            for session_id, paths in candidates.items()
        }


def _read_record(path: Path) -> _DesktopRecord:
    values = _read_top_level_metadata(path)
    desktop_id = _required_string(values, "sessionId", path)
    cli_id = _required_string(values, "cliSessionId", path)
    if path.stem != desktop_id:
        raise ClaudeCodeProviderError(
            f"{path}: filename must match the Desktop sessionId"
        )
    archived = values.get("isArchived")
    if not isinstance(archived, bool):
        raise ClaudeCodeProviderError(f"{path}: isArchived must be a boolean")
    title = values.get("title")
    if title is not None and not isinstance(title, str):
        raise ClaudeCodeProviderError(f"{path}: title must be a string or null")
    return _DesktopRecord(
        desktop_session_id=desktop_id,
        cli_session_id=cli_id,
        cwd=_required_string(values, "cwd", path),
        origin_cwd=_required_string(values, "originCwd", path),
        created_at_ms=_required_integer(values, "createdAt", path),
        last_activity_at_ms=_required_integer(values, "lastActivityAt", path),
        archived=archived,
        title=title,
    )


def _read_top_level_metadata(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as source:
            prefix = source.read(MAX_METADATA_PREFIX_BYTES)
    except (OSError, UnicodeDecodeError) as error:
        raise ClaudeCodeProviderError(f"{path}: cannot read record: {error}") from error
    if not prefix.startswith("{"):
        raise ClaudeCodeProviderError(f"{path}: record must be a JSON object")

    decoder = json.JSONDecoder()
    values: dict[str, Any] = {}
    position = 1
    try:
        while True:
            position = _skip_whitespace(prefix, position)
            key, position = decoder.raw_decode(prefix, position)
            if not isinstance(key, str):
                raise ClaudeCodeProviderError(
                    f"{path}: top-level field name must be a string"
                )
            if not (REQUIRED_FIELDS - values.keys()) and key not in OPTIONAL_FIELDS:
                break
            position = _skip_whitespace(prefix, position)
            if position >= len(prefix) or prefix[position] != ":":
                raise ClaudeCodeProviderError(f"{path}: missing colon after {key}")
            position = _skip_whitespace(prefix, position + 1)
            value, position = decoder.raw_decode(prefix, position)
            if key in REQUIRED_FIELDS | OPTIONAL_FIELDS:
                values[key] = value
            if not (REQUIRED_FIELDS - values.keys()) and (
                OPTIONAL_FIELDS <= values.keys()
            ):
                break
            position = _skip_whitespace(prefix, position)
            if position >= len(prefix) or prefix[position] not in {",", "}"}:
                raise ClaudeCodeProviderError(
                    f"{path}: invalid separator after {key}"
                )
            if prefix[position] == "}":
                break
            position += 1
    except json.JSONDecodeError as error:
        raise ClaudeCodeProviderError(
            f"{path}: required metadata is invalid or exceeds "
            f"{MAX_METADATA_PREFIX_BYTES} bytes"
        ) from error
    missing = REQUIRED_FIELDS - values.keys()
    if missing:
        raise ClaudeCodeProviderError(
            f"{path}: missing required fields: {sorted(missing)}"
        )
    return values


def _skip_whitespace(text: str, position: int) -> int:
    while position < len(text) and text[position].isspace():
        position += 1
    return position


def _required_string(value: dict[str, Any], key: str, path: Path) -> str:
    candidate = value.get(key)
    if not isinstance(candidate, str) or not candidate:
        raise ClaudeCodeProviderError(f"{path}: {key} must be a non-empty string")
    return candidate


def _required_integer(value: dict[str, Any], key: str, path: Path) -> int:
    candidate = value.get(key)
    if isinstance(candidate, bool) or not isinstance(candidate, int) or candidate < 0:
        raise ClaudeCodeProviderError(
            f"{path}: {key} must be a non-negative integer"
        )
    return candidate
