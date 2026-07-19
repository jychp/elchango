#!/usr/bin/env python3
"""POC V3.4: focus one exact Claude Desktop Code session by deep link.

Purpose
=======
Test whether Claude Desktop can focus an existing Code session without cycling
through unrelated sessions and whether the exact target can be verified after
the action.

Method
======
The candidate action is ``claude://code/{session-id}``, documented for Code
sessions by Claude's deep-link guidance. Persistent Desktop records provide a
Desktop ``sessionId``, a Claude Code ``cliSessionId``, and a ``lastFocusedAt``
timestamp. An observed test showed that the Desktop ``local_<uuid>`` identity
only foregrounds Claude. The deep link therefore uses ``cliSessionId``. In
execute mode this POC:

1. snapshots every non-archived session's focus timestamp;
2. opens a deep link containing the exact Claude Code CLI session ID;
3. waits for the target record to acquire a strictly newer ``lastFocusedAt``;
4. verifies that target timestamp is the unique newest timestamp;
5. verifies that Claude Desktop is the frontmost application.

The action is accepted only when every check passes. A successful result proves
one observed focus scenario, not stability across Claude Desktop versions.

Safety and side effects
=======================
Listing mode is read-only. ``--execute`` changes the visible Claude Desktop
selection and may launch or foreground Claude. It never submits a prompt,
changes session content, or controls a running agent. The target must exist,
must not be archived, and must have an observed focus timestamp before the
action. No other session action is safe after an unverified result.

Examples
========
    python scripts/poc/claude/03_claude_desktop_focus.py
    python scripts/poc/claude/03_claude_desktop_focus.py --show-titles
    python scripts/poc/claude/03_claude_desktop_focus.py --session-id local_<uuid>
    python scripts/poc/claude/03_claude_desktop_focus.py \
      --session-id local_<uuid> --execute

Interpretation
==============
``FOCUS_VERIFIED`` means the exact target became uniquely most recently focused
after the deep link and Claude was frontmost. ``FOCUS_NOT_VERIFIED`` means the
post-action evidence was insufficient. ``READ_ONLY_CANDIDATE`` means no action
was attempted.

Official references
===================
https://support.claude.com/en/articles/14729294-open-claude-desktop-with-a-link
https://support.claude.com/en/articles/14898120-open-the-claude-mobile-app-with-a-link
"""

from __future__ import annotations

import argparse
import ctypes
import json
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote


DEFAULT_DESKTOP_ROOT = (
    Path.home() / "Library/Application Support/Claude/claude-code-sessions"
)
DEFAULT_DESKTOP_CONFIG = (
    Path.home() / "Library/Application Support/Claude/claude_desktop_config.json"
)
MAX_METADATA_PREFIX_BYTES = 64 * 1024
REQUIRED_FIELDS = {
    "sessionId",
    "cliSessionId",
    "cwd",
    "lastActivityAt",
    "isArchived",
}
OPTIONAL_FIELDS = {"title", "lastFocusedAt"}
CLAUDE_BUNDLE_ID = "com.anthropic.claudefordesktop"


class ProbeError(RuntimeError):
    """Local data or focus evidence is invalid."""


@dataclass(frozen=True)
class Session:
    desktop_session_id: str
    cli_session_id: str
    cwd: str
    title: str | None
    last_activity_at_ms: int
    last_focused_at_ms: int | None
    archived: bool
    record_path: str


@dataclass(frozen=True)
class FocusResult:
    session_id: str
    deep_link: str
    executed: bool
    focused_before_ms: int | None
    focused_after_ms: int | None
    unique_newest: bool
    claude_frontmost: bool
    elapsed_ms: int
    verdict: str
    message: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Probe exact Claude Desktop Code session focus.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Without --execute this command is read-only. Execute mode visibly\n"
            "changes Claude Desktop focus and requires exact post-action evidence."
        ),
    )
    parser.add_argument(
        "--desktop-sessions-root",
        type=Path,
        default=DEFAULT_DESKTOP_ROOT,
    )
    parser.add_argument(
        "--desktop-config",
        type=Path,
        default=DEFAULT_DESKTOP_CONFIG,
    )
    parser.add_argument("--session-id", help="Exact Desktop local_<uuid> identity.")
    parser.add_argument(
        "--strategy",
        choices=("deep-link", "shortcut"),
        default="deep-link",
        help="Focus mechanism to probe (default: deep-link).",
    )
    parser.add_argument(
        "--shortcut-index",
        type=int,
        help="Cmd+1 through Cmd+9 index used by the shortcut strategy.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Open the deep link and perform exact post-action verification.",
    )
    parser.add_argument("--show-titles", action="store_true")
    parser.add_argument(
        "--sort-by",
        choices=("focus", "activity", "shortcut"),
        default="focus",
        help="Order listing evidence by focus or activity (default: focus).",
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=4.0)
    args = parser.parse_args()
    if args.execute and not args.session_id:
        parser.error("--execute requires --session-id")
    if args.strategy == "shortcut" and (
        args.shortcut_index is None or args.shortcut_index < 1
    ):
        parser.error("--strategy shortcut requires a positive --shortcut-index")
    if args.limit < 0:
        parser.error("--limit must be zero or positive")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    return args


def inventory(root: Path) -> list[Session]:
    if not root.is_dir():
        raise FileNotFoundError(f"Claude Desktop session root not found: {root}")
    sessions: list[Session] = []
    for path in sorted(root.glob("*/*/local_*.json")):
        value = _read_metadata(path)
        session_id = _required_string(value, "sessionId", path)
        if path.stem != session_id:
            raise ProbeError(f"{path}: filename does not match sessionId")
        archived = value.get("isArchived")
        if not isinstance(archived, bool):
            raise ProbeError(f"{path}: isArchived must be a boolean")
        title = value.get("title")
        if title is not None and not isinstance(title, str):
            raise ProbeError(f"{path}: title must be a string or null")
        focused = value.get("lastFocusedAt")
        if focused is not None:
            focused = _integer(focused, "lastFocusedAt", path)
        sessions.append(
            Session(
                desktop_session_id=session_id,
                cli_session_id=_required_string(value, "cliSessionId", path),
                cwd=_required_string(value, "cwd", path),
                title=title,
                last_activity_at_ms=_required_integer(
                    value,
                    "lastActivityAt",
                    path,
                ),
                last_focused_at_ms=focused,
                archived=archived,
                record_path=str(path),
            )
        )
    sessions.sort(
        key=lambda session: (
            -(session.last_focused_at_ms or -1),
            -session.last_activity_at_ms,
            session.desktop_session_id,
        )
    )
    return sessions


def focus(
    sessions: list[Session],
    session_id: str,
    timeout: float,
    strategy: str,
    shortcut_index: int | None,
    shortcut_order: tuple[str, ...],
) -> FocusResult:
    target = next(
        (
            session
            for session in sessions
            if session.desktop_session_id == session_id
        ),
        None,
    )
    if target is None:
        raise ProbeError(f"unknown Claude Desktop session: {session_id}")
    if target.archived:
        raise ProbeError(f"target Claude Desktop session is archived: {session_id}")
    if target.last_focused_at_ms is None:
        raise ProbeError("target has no observed lastFocusedAt baseline")

    visible = [session for session in sessions if not session.archived]
    before_max = max(
        (
            session.last_focused_at_ms
            for session in visible
            if session.last_focused_at_ms is not None
        ),
        default=-1,
    )
    if strategy == "shortcut":
        if shortcut_index is None or shortcut_index > len(shortcut_order):
            raise ProbeError("shortcut index is absent from the persisted sidebar order")
        if shortcut_order[shortcut_index - 1] != session_id:
            raise ProbeError(
                "target does not match the session predicted at the shortcut index"
            )
        deep_link = f"keyboard:cmd+{shortcut_index}"
    else:
        deep_link = f"claude://code/{quote(target.cli_session_id, safe='')}"
    started = time.monotonic()
    if strategy == "shortcut":
        completed = _send_shortcut(shortcut_index)
    else:
        completed = subprocess.run(
            ["/usr/bin/open", deep_link],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    if completed.returncode != 0:
        raise ProbeError(
            f"open failed with exit {completed.returncode}: "
            f"{completed.stderr.strip()}"
        )

    deadline = started + timeout
    focused_after: int | None = None
    unique_newest = False
    claude_frontmost = False
    while time.monotonic() < deadline:
        current = inventory(Path(target.record_path).parents[2])
        current_target = next(
            (
                session
                for session in current
                if session.desktop_session_id == session_id
                and not session.archived
            ),
            None,
        )
        if current_target is None:
            break
        focused_after = current_target.last_focused_at_ms
        current_visible = [session for session in current if not session.archived]
        newest = max(
            (
                session.last_focused_at_ms
                for session in current_visible
                if session.last_focused_at_ms is not None
            ),
            default=-1,
        )
        newest_count = sum(
            session.last_focused_at_ms == newest for session in current_visible
        )
        unique_newest = focused_after == newest and newest_count == 1
        claude_frontmost = _frontmost_bundle_id() == CLAUDE_BUNDLE_ID
        if (
            focused_after is not None
            and focused_after > before_max
            and unique_newest
            and claude_frontmost
        ):
            elapsed_ms = int((time.monotonic() - started) * 1_000)
            return FocusResult(
                session_id=session_id,
                deep_link=deep_link,
                executed=True,
                focused_before_ms=target.last_focused_at_ms,
                focused_after_ms=focused_after,
                unique_newest=True,
                claude_frontmost=True,
                elapsed_ms=elapsed_ms,
                verdict="FOCUS_VERIFIED",
                message="Exact Claude Desktop session focus verified.",
            )
        time.sleep(0.1)

    elapsed_ms = int((time.monotonic() - started) * 1_000)
    return FocusResult(
        session_id=session_id,
        deep_link=deep_link,
        executed=True,
        focused_before_ms=target.last_focused_at_ms,
        focused_after_ms=focused_after,
        unique_newest=unique_newest,
        claude_frontmost=claude_frontmost,
        elapsed_ms=elapsed_ms,
        verdict="FOCUS_NOT_VERIFIED",
        message="Exact target did not acquire sufficient post-action evidence.",
    )


def _frontmost_bundle_id() -> str | None:
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
    if completed.returncode != 0:
        return None
    value = completed.stdout.strip()
    return value or None


def shortcut_order(config_path: Path, sessions: list[Session]) -> tuple[str, ...]:
    """Return Claude's persisted flattened sidebar order for visible sessions."""

    try:
        value = json.loads(config_path.read_text(encoding="utf-8"))
        epitaxy = value["preferences"]["epitaxyPrefs"]
        local_slice = epitaxy["dframe-local-slice"]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError) as error:
        raise ProbeError(f"{config_path}: cannot read shortcut order: {error}") from error
    if not isinstance(local_slice, dict):
        raise ProbeError(f"{config_path}: local sidebar state must be an object")
    visible = [session for session in sessions if not session.archived]
    visible_ids = {
        session.desktop_session_id
        for session in visible
    }
    if "pinnedOrder" in local_slice:
        pinned_order = local_slice["pinnedOrder"]
        if not isinstance(pinned_order, list):
            raise ProbeError(f"{config_path}: pinned order must be a list")
        scope_keys = {
            "/".join(Path(session.record_path).parts[-3:-1])
            for session in visible
        }
        if len(scope_keys) > 1:
            raise ProbeError(
                f"{config_path}: visible sessions span multiple group scopes"
            )
        scopes = epitaxy.get("dframe-group-scopes", {})
        if not isinstance(scopes, dict):
            raise ProbeError(f"{config_path}: group scopes must be an object")
        scope_key = next(iter(scope_keys), None)
        scope = scopes.get(scope_key, {}) if scope_key is not None else {}
        if not isinstance(scope, dict):
            raise ProbeError(f"{config_path}: matching group scope must be an object")
        if scope_key in scopes and any(
            field not in scope for field in ("groups", "assignments", "order")
        ):
            raise ProbeError(f"{config_path}: matching group scope is incomplete")
        groups = scope.get("groups", [])
        assignments = scope.get("assignments", {})
        order = scope.get("order", {})
        if (
            not isinstance(groups, list)
            or not isinstance(assignments, dict)
            or not isinstance(order, dict)
        ):
            raise ProbeError(f"{config_path}: group scope is malformed")
        group_ids: list[str] = []
        for group in groups:
            if (
                not isinstance(group, dict)
                or not isinstance(group.get("id"), str)
                or not group["id"]
            ):
                raise ProbeError(f"{config_path}: group IDs must be nonempty strings")
            group_ids.append(group["id"])
        if any(
            not isinstance(qualified, str) or not isinstance(group_id, str)
            for qualified, group_id in assignments.items()
        ):
            raise ProbeError(f"{config_path}: group assignments must be strings")
        persisted = list(
            dict.fromkeys(
                qualified.removeprefix("code:")
                for qualified in pinned_order
                if isinstance(qualified, str)
                and qualified.startswith("code:")
                and qualified.removeprefix("code:") in visible_ids
                and qualified not in assignments
            )
        )
        for group_id in group_ids:
            qualified_ids = order.get(group_id, [])
            if not isinstance(qualified_ids, list) or any(
                not isinstance(qualified, str) for qualified in qualified_ids
            ):
                raise ProbeError(f"{config_path}: group order must contain string lists")
            for qualified in qualified_ids:
                session_id = qualified.removeprefix("code:")
                if session_id not in visible_ids:
                    continue
                if assignments.get(qualified) != group_id:
                    raise ProbeError(
                        f"{config_path}: group assignment and order disagree"
                    )
                if session_id not in persisted:
                    persisted.append(session_id)
        if any(
            f"code:{session.desktop_session_id}" in assignments
            and session.desktop_session_id not in persisted
            for session in visible
        ):
            raise ProbeError(
                f"{config_path}: group order omits a visible assigned session"
            )
        remaining = sorted(
            (
                session
                for session in visible
                if session.desktop_session_id not in persisted
                and f"code:{session.desktop_session_id}" not in assignments
            ),
            key=lambda session: (
                -session.last_activity_at_ms,
                session.desktop_session_id,
            ),
        )
        return (
            *persisted,
            *(session.desktop_session_id for session in remaining),
        )
    else:
        starred = epitaxy.get("starred-local-code-sessions")
        if not isinstance(starred, list) or any(
            not isinstance(session_id, str) or not session_id
            for session_id in starred
        ):
            raise ProbeError(
                f"{config_path}: starred session order must be a string list"
            )
        assignments = local_slice.get("customGroupAssignments")
        group_order = local_slice.get("customGroupOrder")
        if not isinstance(assignments, dict) or not isinstance(group_order, dict):
            raise ProbeError(f"{config_path}: custom group order must be an object")
        ungrouped = [
            session_id
            for session_id in reversed(starred)
            if session_id in visible_ids
            and f"code:{session_id}" not in assignments
        ]
        grouped: list[str] = []
        for ordered_ids in group_order.values():
            if not isinstance(ordered_ids, list):
                raise ProbeError(f"{config_path}: group order must contain lists")
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
            session
            for session in sessions
            if not session.archived
            and session.desktop_session_id not in persisted_ids
        ),
        key=lambda session: (
            -session.last_activity_at_ms,
            session.desktop_session_id,
        ),
    )
    return (
        *persisted,
        *(session.desktop_session_id for session in remaining),
    )


def _send_shortcut(index: int) -> subprocess.CompletedProcess[str]:
    activated = subprocess.run(
        ["/usr/bin/open", "-b", CLAUDE_BUNDLE_ID],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    if activated.returncode != 0:
        return activated
    time.sleep(0.25)
    direct_index = min(index, 9)
    _post_command_digit(direct_index)
    remaining = index - direct_index
    if remaining:
        time.sleep(0.15)
        _post_control_tab(remaining)
    return subprocess.CompletedProcess(
        args=["CGEvent", f"Cmd+{index}"],
        returncode=0,
        stdout="",
        stderr="",
    )


def _post_command_digit(index: int) -> None:
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
            raise ProbeError("macOS failed to create a keyboard event")
        try:
            set_flags(event, flags)
            post_event(0, event)
        finally:
            release(event)

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
    post_key(55, True, command_flag)
    try:
        key_code = number_key_codes[index]
        post_key(key_code, True, command_flag)
        time.sleep(0.1)
        post_key(key_code, False, command_flag)
    finally:
        post_key(55, False, 0)


def _post_control_tab(steps: int) -> None:
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
            raise ProbeError("macOS failed to create a keyboard event")
        try:
            set_flags(event, flags)
            post_event(0, event)
        finally:
            release(event)

    control_flag = 1 << 18
    post_key(59, True, control_flag)
    try:
        for _ in range(steps):
            post_key(48, True, control_flag)
            time.sleep(0.08)
            post_key(48, False, control_flag)
            time.sleep(0.12)
    finally:
        post_key(59, False, 0)


def _read_metadata(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as source:
            prefix = source.read(MAX_METADATA_PREFIX_BYTES)
    except (OSError, UnicodeDecodeError) as error:
        raise ProbeError(f"{path}: cannot read metadata: {error}") from error
    if not prefix.startswith("{"):
        raise ProbeError(f"{path}: record must be a JSON object")

    decoder = json.JSONDecoder()
    values: dict[str, Any] = {}
    position = 1
    try:
        while True:
            position = _skip_whitespace(prefix, position)
            key, position = decoder.raw_decode(prefix, position)
            if not isinstance(key, str):
                raise ProbeError(f"{path}: field name must be a string")
            if not (REQUIRED_FIELDS - values.keys()) and key not in OPTIONAL_FIELDS:
                break
            position = _skip_whitespace(prefix, position)
            if position >= len(prefix) or prefix[position] != ":":
                raise ProbeError(f"{path}: missing colon after {key}")
            position = _skip_whitespace(prefix, position + 1)
            value, position = decoder.raw_decode(prefix, position)
            if key in REQUIRED_FIELDS | OPTIONAL_FIELDS:
                values[key] = value
            position = _skip_whitespace(prefix, position)
            if position >= len(prefix) or prefix[position] not in {",", "}"}:
                raise ProbeError(f"{path}: invalid separator after {key}")
            if prefix[position] == "}":
                break
            position += 1
    except json.JSONDecodeError as error:
        raise ProbeError(f"{path}: invalid bounded metadata prefix") from error
    missing = REQUIRED_FIELDS - values.keys()
    if missing:
        raise ProbeError(f"{path}: missing required fields: {sorted(missing)}")
    return values


def _skip_whitespace(text: str, position: int) -> int:
    while position < len(text) and text[position].isspace():
        position += 1
    return position


def _required_string(value: dict[str, Any], key: str, path: Path) -> str:
    candidate = value.get(key)
    if not isinstance(candidate, str) or not candidate:
        raise ProbeError(f"{path}: {key} must be a non-empty string")
    return candidate


def _required_integer(value: dict[str, Any], key: str, path: Path) -> int:
    return _integer(value.get(key), key, path)


def _integer(value: object, key: str, path: Path) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ProbeError(f"{path}: {key} must be a non-negative integer")
    return value


def main() -> int:
    args = parse_args()
    try:
        sessions = inventory(args.desktop_sessions_root)
        needs_shortcut_order = args.sort_by == "shortcut" or (
            args.execute and args.strategy == "shortcut"
        )
        persisted_shortcut_order = (
            shortcut_order(args.desktop_config, sessions)
            if needs_shortcut_order
            else ()
        )
        if args.sort_by == "activity":
            sessions.sort(
                key=lambda session: (
                    -session.last_activity_at_ms,
                    session.desktop_session_id,
                )
            )
        elif args.sort_by == "shortcut":
            by_id = {
                session.desktop_session_id: session for session in sessions
            }
            sessions = [
                by_id[session_id] for session_id in persisted_shortcut_order
            ]
        visible = [session for session in sessions if not session.archived]
        if args.session_id and args.execute:
            result = focus(
                sessions,
                args.session_id,
                args.timeout,
                args.strategy,
                args.shortcut_index,
                persisted_shortcut_order,
            )
            print(
                json.dumps(asdict(result), indent=2, sort_keys=True)
                if args.json
                else (
                    f"{result.verdict}: {result.message}\n"
                    f"session={result.session_id} elapsed_ms={result.elapsed_ms} "
                    f"focused_before={result.focused_before_ms} "
                    f"focused_after={result.focused_after_ms}"
                )
            )
            return 0 if result.verdict == "FOCUS_VERIFIED" else 1

        if args.session_id and not any(
            session.desktop_session_id == args.session_id for session in visible
        ):
            raise ProbeError(f"unknown non-archived session: {args.session_id}")
        listed = (
            [
                session
                for session in visible
                if args.session_id is None
                or session.desktop_session_id == args.session_id
            ]
        )
        if args.limit:
            listed = listed[: args.limit]
        payload = [
            {
                **asdict(session),
                "title": session.title if args.show_titles else None,
            }
            for session in listed
        ]
        if args.json:
            print(
                json.dumps(
                    {
                        "verdict": "READ_ONLY_CANDIDATE",
                        "sessions": payload,
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            print("READ_ONLY_CANDIDATE")
            for session in listed:
                title = session.title if args.show_titles else "<hidden>"
                print(
                    f"{session.desktop_session_id} "
                    f"focused={session.last_focused_at_ms} title={title}"
                )
        return 0
    except (FileNotFoundError, ProbeError, subprocess.TimeoutExpired) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
