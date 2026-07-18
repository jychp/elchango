"""Read-only Claude Desktop Code inventory backed by persistent local records."""

from __future__ import annotations

import ctypes
import json
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from elchango.claude_activity import ClaudeActivityStore
from elchango.models import (
    AgentSession,
    ButtonIcon,
    ProviderCapability,
    ProviderSnapshot,
)
from elchango.providers.base import ProviderActionResult, ProviderError


DEFAULT_DESKTOP_SESSIONS_ROOT = (
    Path.home() / "Library/Application Support/Claude/claude-code-sessions"
)
DEFAULT_CLAUDE_PROJECTS = Path.home() / ".claude/projects"
DEFAULT_CLAUDE_DESKTOP_CONFIG = (
    Path.home() / "Library/Application Support/Claude/claude_desktop_config.json"
)
CLAUDE_BUNDLE_ID = "com.anthropic.claudefordesktop"
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
OPTIONAL_FIELDS = {"title", "lastFocusedAt"}


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
    last_focused_at_ms: int | None
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
    display_name: ClassVar[str] = "Claude"
    icon: ClassVar[ButtonIcon] = "claude"
    capabilities: ClassVar[frozenset[ProviderCapability]] = frozenset(
        {"focus_session", "new_session"}
    )

    def __init__(
        self,
        desktop_sessions_root: Path = DEFAULT_DESKTOP_SESSIONS_ROOT,
        projects_root: Path = DEFAULT_CLAUDE_PROJECTS,
        desktop_config: Path = DEFAULT_CLAUDE_DESKTOP_CONFIG,
        activity_store: ClaudeActivityStore | None = None,
    ) -> None:
        self._desktop_sessions_root = desktop_sessions_root
        self._projects_root = projects_root
        self._desktop_config = desktop_config
        self.activity_store = activity_store or ClaudeActivityStore()
        self._cache: dict[Path, _CachedRecord] = {}
        self._lock = threading.Lock()
        self._focus_lock = threading.Lock()
        self._launch_lock = threading.Lock()

    def snapshot(self) -> ProviderSnapshot:
        """Read persistent non-archived sessions and overlay fresh hook signals."""

        observed_at_ms = time.time_ns() // 1_000_000
        records = self._records()
        transcripts = self._transcripts_by_session_id()
        visible_records = tuple(record for record in records if not record.archived)
        try:
            focusable_ids = frozenset(
                _shortcut_order(self._desktop_config, visible_records)
            )
        except ClaudeCodeProviderError:
            focusable_ids = frozenset()
        newest_focus = max(
            (
                record.last_focused_at_ms
                for record in visible_records
                if record.last_focused_at_ms is not None
            ),
            default=None,
        )
        selected_native_session_id = None
        if newest_focus is not None:
            selected = tuple(
                record
                for record in visible_records
                if record.last_focused_at_ms == newest_focus
            )
            if len(selected) == 1:
                selected_native_session_id = selected[0].desktop_session_id
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
                    capabilities=(
                        self.capabilities
                        if record.desktop_session_id in focusable_ids
                        else frozenset()
                    ),
                    icon="claude",
                    title=record.title or "Untitled Claude session",
                    workspace_id=record.origin_cwd,
                    workspace_path=record.cwd,
                    state=session_state,
                    confidence=confidence,
                    state_detail=detail,
                    selected=(
                        record.desktop_session_id == selected_native_session_id
                    ),
                    last_activity_at_ms=record.last_activity_at_ms,
                )
            )
        sessions.sort(key=lambda session: (-session.last_activity_at_ms, session.id))
        return ProviderSnapshot(
            provider_id=self.provider_id,
            capabilities=self.capabilities,
            observed_at_ms=observed_at_ms,
            selected_native_session_id=selected_native_session_id,
            sessions=tuple(sessions),
            source=str(self._desktop_sessions_root),
        )

    def focus(self, native_session_id: str) -> ProviderActionResult:
        """Focus one exact Desktop session through persisted sidebar shortcuts."""

        with self._focus_lock:
            return self._focus(native_session_id)

    def _focus(self, native_session_id: str) -> ProviderActionResult:
        started = time.monotonic()
        with self._lock:
            records = self._records_locked()
            visible = tuple(record for record in records if not record.archived)
            target = next(
                (
                    record
                    for record in visible
                    if record.desktop_session_id == native_session_id
                ),
                None,
            )
            if target is None:
                raise ClaudeCodeProviderError(
                    f"unknown non-archived Claude session: {native_session_id}"
                )
            order = _shortcut_order(self._desktop_config, visible)
            try:
                shortcut_index = order.index(native_session_id) + 1
            except ValueError:
                return _focus_result(
                    started,
                    native_session_id,
                    None,
                    False,
                    "FOCUS_UNSUPPORTED",
                    "Claude session is absent from the persisted sidebar shortcuts.",
                )
            before_max = max(
                (
                    record.last_focused_at_ms
                    for record in visible
                    if record.last_focused_at_ms is not None
                ),
                default=-1,
            )
            target_already_selected = (
                target.last_focused_at_ms == before_max
                and sum(
                    record.last_focused_at_ms == before_max for record in visible
                )
                == 1
            )

        if target_already_selected:
            _activate_claude()
        else:
            _send_focus_shortcut(shortcut_index)
        deadline = time.monotonic() + 4.0
        while time.monotonic() < deadline:
            with self._lock:
                current = tuple(
                    record
                    for record in self._records_locked()
                    if not record.archived
                )
            current_target = next(
                (
                    record
                    for record in current
                    if record.desktop_session_id == native_session_id
                ),
                None,
            )
            if current_target is None:
                break
            newest = max(
                (
                    record.last_focused_at_ms
                    for record in current
                    if record.last_focused_at_ms is not None
                ),
                default=-1,
            )
            uniquely_newest = (
                current_target.last_focused_at_ms == newest
                and sum(
                    record.last_focused_at_ms == newest for record in current
                )
                == 1
            )
            if (
                current_target.last_focused_at_ms is not None
                and (
                    current_target.last_focused_at_ms > before_max
                    or target_already_selected
                )
                and uniquely_newest
                and _claude_is_frontmost()
            ):
                self.activity_store.acknowledge(
                    target.cli_session_id,
                    time.time_ns() // 1_000_000,
                )
                return _focus_result(
                    started,
                    native_session_id,
                    shortcut_index,
                    True,
                    "FOCUS_VERIFIED",
                    "Exact Claude Desktop session focus verified.",
                )
            time.sleep(0.1)
        return _focus_result(
            started,
            native_session_id,
            shortcut_index,
            True,
            "FOCUS_UNVERIFIED",
            "Claude Desktop did not select the exact target before timeout.",
        )

    def open_new(self) -> ProviderActionResult:
        """Open Claude Desktop's official new Code session deep link."""

        with self._launch_lock:
            deep_link = "claude://code/new"
            started = time.monotonic()
            try:
                completed = subprocess.run(
                    ["/usr/bin/open", deep_link],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as error:
                raise ClaudeCodeProviderError(
                    f"cannot open Claude Desktop new session link: {error}"
                ) from error
            if completed.returncode != 0:
                raise ClaudeCodeProviderError(
                    "cannot open Claude Desktop new session link: "
                    f"{completed.stderr.strip()}"
                )
            return ProviderActionResult(
                accepted=True,
                verdict="NEW_SESSION_REQUESTED",
                details={
                    "executed": True,
                    "deep_link": deep_link,
                    "elapsed_ms": round((time.monotonic() - started) * 1_000),
                    "message": "Claude Desktop new Code session requested.",
                },
            )

    def record_hook(
        self,
        payload: dict[str, Any],
        observed_at_ms: int,
    ) -> object:
        """Record hook evidence only for a current persistent Code session."""

        session_id = payload.get("session_id")
        with self._lock:
            known_session_ids = {
                record.cli_session_id
                for record in self._records_locked()
                if not record.archived
            }
        if session_id not in known_session_ids:
            raise ValueError("Claude Code hook session_id is not in current inventory")
        return self.activity_store.record(payload, observed_at_ms)

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
            return self._records_locked(paths)

    def _records_locked(
        self,
        paths: tuple[Path, ...] | None = None,
    ) -> tuple[_DesktopRecord, ...]:
        if paths is None:
            paths = tuple(
                sorted(self._desktop_sessions_root.glob("*/*/local_*.json"))
            )
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
    last_focused_at = values.get("lastFocusedAt")
    if last_focused_at is not None:
        last_focused_at = _integer(last_focused_at, "lastFocusedAt", path)
    return _DesktopRecord(
        desktop_session_id=desktop_id,
        cli_session_id=cli_id,
        cwd=_required_string(values, "cwd", path),
        origin_cwd=_required_string(values, "originCwd", path),
        created_at_ms=_required_integer(values, "createdAt", path),
        last_activity_at_ms=_required_integer(values, "lastActivityAt", path),
        last_focused_at_ms=last_focused_at,
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
    return _integer(value.get(key), key, path)


def _integer(candidate: object, key: str, path: Path) -> int:
    if isinstance(candidate, bool) or not isinstance(candidate, int) or candidate < 0:
        raise ClaudeCodeProviderError(
            f"{path}: {key} must be a non-negative integer"
        )
    return candidate


def _shortcut_order(
    config_path: Path,
    records: tuple[_DesktopRecord, ...],
) -> tuple[str, ...]:
    try:
        value = json.loads(config_path.read_text(encoding="utf-8"))
        epitaxy = value["preferences"]["epitaxyPrefs"]
        starred = epitaxy["starred-local-code-sessions"]
        local_slice = epitaxy["dframe-local-slice"]
        assignments = local_slice["customGroupAssignments"]
        group_order = local_slice["customGroupOrder"]
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
    ) as error:
        raise ClaudeCodeProviderError(
            f"{config_path}: cannot read Claude sidebar order: {error}"
        ) from error
    if not isinstance(starred, list) or any(
        not isinstance(session_id, str) or not session_id for session_id in starred
    ):
        raise ClaudeCodeProviderError(
            f"{config_path}: starred session order must be a string list"
        )
    if not isinstance(assignments, dict) or not isinstance(group_order, dict):
        raise ClaudeCodeProviderError(
            f"{config_path}: custom group order must be an object"
        )

    visible_ids = {record.desktop_session_id for record in records}
    ungrouped = [
        session_id
        for session_id in reversed(starred)
        if session_id in visible_ids
        and f"code:{session_id}" not in assignments
    ]
    grouped: list[str] = []
    for ordered_ids in group_order.values():
        if not isinstance(ordered_ids, list):
            raise ClaudeCodeProviderError(
                f"{config_path}: custom group entries must be lists"
            )
        grouped.extend(
            qualified.removeprefix("code:")
            for qualified in ordered_ids
            if isinstance(qualified, str)
            and qualified.startswith("code:")
            and qualified.removeprefix("code:") in visible_ids
        )
    persisted = tuple(dict.fromkeys([*ungrouped, *grouped]))
    persisted_ids = set(persisted)
    remaining = sorted(
        (
            record
            for record in records
            if record.desktop_session_id not in persisted_ids
        ),
        key=lambda record: (
            -record.last_activity_at_ms,
            record.desktop_session_id,
        ),
    )
    return (
        *persisted,
        *(record.desktop_session_id for record in remaining),
    )


def _send_focus_shortcut(index: int) -> None:
    _activate_claude()
    time.sleep(0.25)
    direct_index = min(index, 9)
    number_key_codes = {
        1: 18,
        2: 19,
        3: 20,
        4: 21,
        5: 23,
        6: 22,
        7: 26,
        8: 28,
        9: 25,
    }
    _post_chord(55, 1 << 20, number_key_codes[direct_index])
    remaining = index - direct_index
    if remaining:
        time.sleep(0.15)
        for _ in range(remaining):
            _post_chord(59, 1 << 18, 48)
            time.sleep(0.12)


def _activate_claude() -> None:
    completed = subprocess.run(
        ["/usr/bin/open", "-b", CLAUDE_BUNDLE_ID],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    if completed.returncode != 0:
        raise ClaudeCodeProviderError(
            f"cannot activate Claude Desktop: {completed.stderr.strip()}"
        )


def _post_chord(modifier_key: int, modifier_flag: int, key_code: int) -> None:
    application_services = ctypes.CDLL(
        "/System/Library/Frameworks/ApplicationServices.framework/"
        "ApplicationServices"
    )
    core_foundation = ctypes.CDLL(
        "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
    )
    create_event = application_services.CGEventCreateKeyboardEvent
    create_event.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint16,
        ctypes.c_bool,
    ]
    create_event.restype = ctypes.c_void_p
    set_flags = application_services.CGEventSetFlags
    set_flags.argtypes = [ctypes.c_void_p, ctypes.c_uint64]
    post_event = application_services.CGEventPost
    post_event.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
    release = core_foundation.CFRelease
    release.argtypes = [ctypes.c_void_p]

    def post_key(code: int, down: bool, flags: int) -> None:
        event = create_event(None, code, down)
        if not event:
            raise ClaudeCodeProviderError("macOS failed to create a keyboard event")
        try:
            set_flags(event, flags)
            post_event(0, event)
        finally:
            release(event)

    post_key(modifier_key, True, modifier_flag)
    try:
        post_key(key_code, True, modifier_flag)
        time.sleep(0.08)
        post_key(key_code, False, modifier_flag)
    finally:
        post_key(modifier_key, False, 0)


def _claude_is_frontmost() -> bool:
    completed = subprocess.run(
        [
            "/usr/bin/osascript",
            "-e",
            (
                'tell application "System Events" to get bundle identifier '
                "of first application process whose frontmost is true"
            ),
        ],
        capture_output=True,
        text=True,
        timeout=2,
        check=False,
    )
    return (
        completed.returncode == 0
        and completed.stdout.strip() == CLAUDE_BUNDLE_ID
    )


def _focus_result(
    started: float,
    session_id: str,
    shortcut_index: int | None,
    executed: bool,
    verdict: str,
    message: str,
) -> ProviderActionResult:
    return ProviderActionResult(
        accepted=verdict == "FOCUS_VERIFIED",
        verdict=verdict,
        details={
            "session_id": session_id,
            "strategy": "sidebar_shortcut",
            "shortcut_index": shortcut_index,
            "executed": executed,
            "elapsed_ms": round((time.monotonic() - started) * 1_000),
            "verdict": verdict,
            "message": message,
        },
    )
