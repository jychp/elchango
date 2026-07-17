"""Best-effort Cursor session focus with exact post-action verification."""

from __future__ import annotations

import ctypes
import json
import sqlite3
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from elchango.providers.cursor import (
    DEFAULT_DATABASE,
    DEFAULT_WORKSPACE_STORAGE,
    SELECTED_AGENT_KEY,
    CursorProviderError,
)


MEMBERSHIP_KEY = "glass.localAgentProjectMembership.v1"
SIDEBAR_SETTINGS_KEY = "cursor/glassSidebarSettings"
PINNED_COMPOSERS_KEY = "cursor/pinnedComposers"
FOCUS_REQUIRED_TABLES = {"ItemTable", "composerHeaders"}


@dataclass(frozen=True, slots=True)
class FocusResult:
    """Conservative result returned after one user-requested focus attempt."""

    session_id: str
    selected_before: str | None
    selected_after: str | None
    strategy: str
    shortcut_index: int | None
    executed: bool
    cursor_frontmost: bool | None
    elapsed_ms: int
    verdict: str
    message: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class CursorFocusController:
    """Serialize verified macOS focus attempts through Cursor's sidebar shortcuts."""

    def __init__(
        self,
        database: Path = DEFAULT_DATABASE,
        workspace_storage: Path = DEFAULT_WORKSPACE_STORAGE,
        *,
        timeout: float = 5.0,
        interval: float = 0.1,
    ) -> None:
        self._database = database
        self._workspace_storage = workspace_storage
        self._timeout = timeout
        self._interval = interval
        self._lock = threading.Lock()

    def focus(self, session_id: str) -> FocusResult:
        with self._lock:
            return self._focus_locked(session_id)

    def _focus_locked(self, session_id: str) -> FocusResult:
        started = time.monotonic()
        if sys.platform != "darwin":
            raise CursorProviderError("Cursor focus is supported only on macOS")

        connection = _connect_read_only(self._database)
        try:
            _validate_schema(connection)
            target_error = _validate_target(connection, session_id)
            selected_before = _read_selected_id(connection)
            if target_error is not None:
                return _result(
                    started,
                    session_id,
                    selected_before,
                    selected_before,
                    "none_invalid_target",
                    None,
                    False,
                    None,
                    "INVALID_TARGET",
                    target_error,
                )

            _activate_cursor()
            time.sleep(0.2)
            selected_before = _read_selected_id(connection)
            strategy = (
                "activate_application"
                if selected_before == session_id
                else "sidebar_shortcut"
            )
            shortcut_index = None
            if selected_before != session_id:
                shortcut_index = _shortcut_index(
                    session_id,
                    tuple(
                        _read_sidebar_order(
                            connection,
                            self._workspace_storage,
                        )
                    ),
                )
                if shortcut_index is None:
                    return _result(
                        started,
                        session_id,
                        selected_before,
                        selected_before,
                        "none_target_absent_from_sidebar_shortcuts",
                        None,
                        True,
                        _cursor_is_frontmost(),
                        "UNSUPPORTED_SIDEBAR_SHORTCUT",
                        "The target is absent from Cursor's current sidebar order.",
                    )
                frontmost = _frontmost_application()
                if frontmost != "Cursor":
                    return _result(
                        started,
                        session_id,
                        selected_before,
                        _read_selected_id(connection),
                        "none_cursor_not_foreground",
                        shortcut_index,
                        True,
                        False if frontmost is not None else None,
                        "CURSOR_NOT_FOREGROUND",
                        "Cursor was not foreground before keyboard injection.",
                    )
                _send_sidebar_shortcut(shortcut_index)

            selected_after, cursor_frontmost = _verify_focus(
                connection,
                session_id,
                self._timeout,
                self._interval,
            )
            if selected_after != session_id:
                return _result(
                    started,
                    session_id,
                    selected_before,
                    selected_after,
                    strategy,
                    shortcut_index,
                    True,
                    cursor_frontmost,
                    "FOCUS_UNVERIFIED",
                    "Cursor did not select the requested session before timeout.",
                )
            if cursor_frontmost is not True:
                return _result(
                    started,
                    session_id,
                    selected_before,
                    selected_after,
                    strategy,
                    shortcut_index,
                    True,
                    cursor_frontmost,
                    "TARGET_SELECTED_CURSOR_NOT_FOREGROUND",
                    "The session was selected, but Cursor foreground was not verified.",
                )
            return _result(
                started,
                session_id,
                selected_before,
                selected_after,
                strategy,
                shortcut_index,
                True,
                True,
                "FOCUS_VERIFIED",
                "The requested session is selected and Cursor is foreground.",
            )
        except sqlite3.Error as error:
            raise CursorProviderError(
                f"Cursor focus database read failed: {error}"
            ) from error
        finally:
            connection.close()


def _result(
    started: float,
    session_id: str,
    selected_before: str | None,
    selected_after: str | None,
    strategy: str,
    shortcut_index: int | None,
    executed: bool,
    cursor_frontmost: bool | None,
    verdict: str,
    message: str,
) -> FocusResult:
    return FocusResult(
        session_id=session_id,
        selected_before=selected_before,
        selected_after=selected_after,
        strategy=strategy,
        shortcut_index=shortcut_index,
        executed=executed,
        cursor_frontmost=cursor_frontmost,
        elapsed_ms=round((time.monotonic() - started) * 1_000),
        verdict=verdict,
        message=message,
    )


def _connect_read_only(database: Path) -> sqlite3.Connection:
    if not database.is_file():
        raise CursorProviderError(f"Cursor database not found: {database}")
    connection = sqlite3.connect(
        f"{database.resolve().as_uri()}?mode=ro",
        uri=True,
        timeout=2,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _validate_schema(connection: sqlite3.Connection) -> None:
    tables = {
        row["name"]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    missing = FOCUS_REQUIRED_TABLES - tables
    if missing:
        raise CursorProviderError(
            f"Unsupported Cursor focus schema; missing tables: {sorted(missing)}"
        )


def _validate_target(
    connection: sqlite3.Connection,
    session_id: str,
) -> str | None:
    row = connection.execute(
        """
        SELECT isArchived, isSubagent, value
        FROM composerHeaders
        WHERE composerId = ?
        """,
        (session_id,),
    ).fetchone()
    if row is None:
        return "The requested session no longer exists."
    header = _parse_object(row["value"])
    if bool(row["isArchived"]):
        return "The requested session is archived."
    if bool(row["isSubagent"]):
        return "Subagents cannot be focused from the deck."
    if bool(header.get("isDraft", False)):
        return "Draft sessions cannot be focused from the deck."
    if bool(header.get("isEphemeral", False)):
        return "Ephemeral sessions cannot be focused from the deck."
    return None


def _read_selected_id(connection: sqlite3.Connection) -> str | None:
    row = connection.execute(
        "SELECT value FROM ItemTable WHERE key = ?",
        (SELECTED_AGENT_KEY,),
    ).fetchone()
    if row is None:
        return None
    value = _decode_text(row["value"])
    return value.strip() if value and value.strip() else None


@dataclass(frozen=True, slots=True)
class _SidebarCandidate:
    session_id: str
    updated_at: int
    created_at: int
    workspace_id: str | None
    repository_name: str | None


def _read_sidebar_order(
    connection: sqlite3.Connection,
    workspace_storage: Path,
) -> list[str]:
    """Reproduce Cursor's local-agent keyboard navigation order."""

    settings_row = connection.execute(
        "SELECT value FROM ItemTable WHERE key = ?",
        (SIDEBAR_SETTINGS_KEY,),
    ).fetchone()
    settings = _parse_object(settings_row["value"]) if settings_row else {}
    if settings.get("groupBy") != "repository":
        raise CursorProviderError(
            "Cursor sidebar shortcuts require repository grouping"
        )
    sort_by = settings.get("sortAgentsBy")
    if sort_by not in {"updated", "created"}:
        raise CursorProviderError(
            f"Unsupported Cursor sidebar sort: {sort_by!r}"
        )
    section_order_by_group = settings.get("sectionOrderByGroupBy")
    if not isinstance(section_order_by_group, dict):
        raise CursorProviderError("Cursor sidebar section order is missing")
    section_order = section_order_by_group.get("repository")
    if not isinstance(section_order, list) or not all(
        isinstance(section_id, str) for section_id in section_order
    ):
        raise CursorProviderError("Cursor repository section order is invalid")

    membership_row = connection.execute(
        "SELECT value FROM ItemTable WHERE key = ?",
        (MEMBERSHIP_KEY,),
    ).fetchone()
    if membership_row is None:
        return []
    memberships = _parse_object(membership_row["value"])

    candidates: list[_SidebarCandidate] = []
    rows = connection.execute(
        """
        SELECT composerId, createdAt, lastUpdatedAt,
               isArchived, isSubagent, value
        FROM composerHeaders
        """
    )
    for row in rows:
        session_id = str(row["composerId"])
        if session_id not in memberships:
            continue
        if bool(row["isArchived"]) or bool(row["isSubagent"]):
            continue
        header = _parse_object(row["value"])
        if bool(header.get("isDraft", False)) or bool(
            header.get("isEphemeral", False)
        ):
            continue
        candidates.append(
            _SidebarCandidate(
                session_id=session_id,
                updated_at=_integer(row["lastUpdatedAt"]),
                created_at=_integer(row["createdAt"]),
                workspace_id=_workspace_id(header),
                repository_name=_repository_name(header),
            )
        )

    key = (
        (lambda candidate: candidate.updated_at)
        if sort_by == "updated"
        else (lambda candidate: candidate.created_at)
    )
    pinned_ids = _read_pinned_ids(workspace_storage)
    pinned = sorted(
        (candidate for candidate in candidates if candidate.session_id in pinned_ids),
        key=key,
        reverse=True,
    )

    groups: dict[str, list[_SidebarCandidate]] = {}
    for candidate in candidates:
        if candidate.session_id in pinned_ids:
            continue
        section_id = _candidate_section(candidate, section_order)
        if section_id is not None:
            groups.setdefault(section_id, []).append(candidate)

    ordered = list(pinned)
    for section_id in section_order:
        ordered.extend(
            sorted(groups.get(section_id, ()), key=key, reverse=True)
        )
    return [candidate.session_id for candidate in ordered]


def _shortcut_index(
    target_id: str,
    sidebar_order: tuple[str, ...],
) -> int | None:
    try:
        return sidebar_order.index(target_id) + 1
    except ValueError:
        return None


def _read_pinned_ids(workspace_storage: Path) -> set[str]:
    database = workspace_storage / "empty-window" / "state.vscdb"
    if not database.is_file():
        return set()
    connection = _connect_read_only(database)
    try:
        row = connection.execute(
            "SELECT value FROM ItemTable WHERE key = ?",
            (PINNED_COMPOSERS_KEY,),
        ).fetchone()
        if row is None:
            return set()
        text = _decode_text(row["value"])
        payload = json.loads(text) if text is not None else []
        return {
            item for item in payload if isinstance(item, str)
        } if isinstance(payload, list) else set()
    except (json.JSONDecodeError, sqlite3.Error):
        return set()
    finally:
        connection.close()


def _candidate_section(
    candidate: _SidebarCandidate,
    section_order: list[str],
) -> str | None:
    if candidate.repository_name:
        suffix = f"/{candidate.repository_name}"
        matches = [
            section_id
            for section_id in section_order
            if section_id.startswith("repo:")
            and "|" not in section_id
            and section_id.endswith(suffix)
        ]
        if len(matches) == 1:
            return matches[0]
    workspace_section = (
        f"workspace:{candidate.workspace_id}"
        if candidate.workspace_id
        else None
    )
    return workspace_section if workspace_section in section_order else None


def _workspace_id(header: dict[str, Any]) -> str | None:
    workspace = header.get("workspaceIdentifier")
    value = workspace.get("id") if isinstance(workspace, dict) else None
    return value if isinstance(value, str) and value else None


def _repository_name(header: dict[str, Any]) -> str | None:
    location = header.get("agentLocation")
    if isinstance(location, dict):
        source = location.get("sourceRepoRootPath")
        if isinstance(source, str) and source:
            return Path(source).name
    repositories = header.get("trackedGitRepos")
    if isinstance(repositories, list) and repositories:
        repository = repositories[0]
        path = repository.get("repoPath") if isinstance(repository, dict) else None
        if isinstance(path, str) and path:
            parts = Path(path).parts
            if "worktrees" in parts:
                index = parts.index("worktrees")
                if index + 1 < len(parts):
                    return parts[index + 1]
            return Path(path).name
    return None


def _integer(value: Any) -> int:
    return value if isinstance(value, int) else 0


def _activate_cursor() -> None:
    result = subprocess.run(
        ["open", "-a", "Cursor"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "no output"
        raise CursorProviderError(
            f"Cursor activation failed with exit {result.returncode}: {detail}"
        )


def _frontmost_application() -> str | None:
    result = subprocess.run(
        [
            "osascript",
            "-e",
            (
                'tell application "System Events" to get name of first '
                "application process whose frontmost is true"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=3,
    )
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


def _cursor_is_frontmost() -> bool | None:
    frontmost = _frontmost_application()
    return frontmost == "Cursor" if frontmost is not None else None


def _send_sidebar_shortcut(index: int) -> None:
    if index < 1:
        raise ValueError("Cursor sidebar shortcut index must be positive")
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

    def post_key(key_code: int, key_down: bool, flags: int) -> None:
        event = create_event(None, key_code, key_down)
        if not event:
            raise CursorProviderError("macOS failed to create a keyboard event")
        try:
            set_flags(event, flags)
            post_event(0, event)
        finally:
            release(event)

    command_key = 55
    option_key = 58
    down_arrow_key = 125
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
    command_flag = 1 << 20
    option_flag = 1 << 19
    direct_index = min(index, 9)
    post_key(command_key, True, command_flag)
    try:
        key_code = number_key_codes[direct_index]
        post_key(key_code, True, command_flag)
        time.sleep(0.1)
        post_key(key_code, False, command_flag)
    finally:
        post_key(command_key, False, 0)

    remaining_steps = index - direct_index
    if remaining_steps == 0:
        return
    time.sleep(0.15)
    post_key(option_key, True, option_flag)
    try:
        for _ in range(remaining_steps):
            post_key(down_arrow_key, True, option_flag)
            time.sleep(0.05)
            post_key(down_arrow_key, False, option_flag)
            time.sleep(0.08)
    finally:
        post_key(option_key, False, 0)


def _verify_focus(
    connection: sqlite3.Connection,
    target_id: str,
    timeout: float,
    interval: float,
) -> tuple[str | None, bool | None]:
    deadline = time.monotonic() + timeout
    selected = _read_selected_id(connection)
    frontmost = _frontmost_application()
    while (
        (selected != target_id or frontmost != "Cursor")
        and time.monotonic() < deadline
    ):
        time.sleep(min(interval, max(0, deadline - time.monotonic())))
        selected = _read_selected_id(connection)
        frontmost = _frontmost_application()
    return selected, frontmost == "Cursor" if frontmost is not None else None


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
