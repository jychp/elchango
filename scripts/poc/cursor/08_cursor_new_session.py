#!/usr/bin/env python3
"""POC M0.8: create exactly one Cursor agent and verify its identity.

Purpose
-------
Test whether Cursor's native Agents Window shortcuts provide a conservative
session-launch primitive for elChango.

Method
------
The POC snapshots all top-level ``composerHeaders`` IDs and the exact
``cursor/glass.selectedAgent`` value using a read-only SQLite connection. With
explicit execution confirmation, it activates Cursor, sends ``Option+Cmd+N`` to
open or focus the Agents Window, then sends ``Cmd+N`` exactly once. It polls the
database until exactly one new top-level composer appears and verifies that
Cursor selected that same ID.

Safety
------
The default mode is observation-only. Creation requires both ``--execute`` and
``--confirm CREATE_CURSOR_SESSION``. Keyboard injection is refused unless
Cursor is frontmost. The POC never writes SQLite and never retries ``Cmd+N``.

Interpretation
--------------
``LAUNCH_VERIFIED`` means one and only one new top-level composer appeared and
became Cursor's selected agent. Every other verdict is unsafe for automatic
launch and must not be retried blindly.

Limitations
-----------
Cursor shortcuts, tables, keys, and JSON fields are undocumented. Concurrent
manual session creation makes attribution ambiguous. Database persistence may
lag the UI, and selected-agent state does not prove text-input focus.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import sqlite3
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


DEFAULT_DATABASE = (
    Path.home()
    / "Library/Application Support/Cursor/User/globalStorage/state.vscdb"
)
SELECTED_AGENT_KEY = "cursor/glass.selectedAgent"
REQUIRED_TABLES = {"ItemTable", "composerHeaders"}
CONFIRMATION = "CREATE_CURSOR_SESSION"


@dataclass(frozen=True, slots=True)
class Composer:
    composer_id: str
    workspace_id: str | None
    archived: bool
    draft: bool


@dataclass(frozen=True, slots=True)
class LaunchObservation:
    verdict: str
    message: str
    executed: bool
    selected_before: str | None
    selected_after: str | None
    new_composer_ids: tuple[str, ...]
    elapsed_ms: int
    query_only: bool = True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=DEFAULT_DATABASE,
        help=f"Cursor global state database (default: {DEFAULT_DATABASE}).",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Inject one native New Agent shortcut sequence.",
    )
    parser.add_argument(
        "--confirm",
        help=f"Required with --execute; must equal {CONFIRMATION!r}.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="Seconds to wait for the new composer (default: 5).",
    )
    parser.add_argument(
        "--settle",
        type=float,
        default=0.5,
        help="Seconds to watch for a second composer after detection (default: 0.5).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print one machine-readable JSON result.",
    )
    return parser.parse_args()


def connect_read_only(database: Path) -> sqlite3.Connection:
    if not database.is_file():
        raise RuntimeError(f"Cursor database not found: {database}")
    connection = sqlite3.connect(
        f"{database.resolve().as_uri()}?mode=ro",
        uri=True,
        timeout=2,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    tables = {
        row["name"]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    missing = REQUIRED_TABLES - tables
    if missing:
        connection.close()
        raise RuntimeError(
            f"Unsupported Cursor schema; missing tables: {sorted(missing)}"
        )
    return connection


def read_composers(database: Path) -> dict[str, Composer]:
    connection = connect_read_only(database)
    try:
        composers: dict[str, Composer] = {}
        for row in connection.execute(
            """
            SELECT composerId, workspaceId, isArchived, isSubagent, value
            FROM composerHeaders
            """
        ):
            if bool(row["isSubagent"]):
                continue
            payload = parse_object(row["value"])
            composer_id = str(row["composerId"])
            composers[composer_id] = Composer(
                composer_id=composer_id,
                workspace_id=(
                    str(row["workspaceId"]) if row["workspaceId"] else None
                ),
                archived=bool(row["isArchived"]),
                draft=bool(payload.get("isDraft", False)),
            )
        return composers
    finally:
        connection.close()


def read_selected_id(database: Path) -> str | None:
    connection = connect_read_only(database)
    try:
        row = connection.execute(
            "SELECT value FROM ItemTable WHERE key = ?",
            (SELECTED_AGENT_KEY,),
        ).fetchone()
        if row is None:
            return None
        value = decode_text(row["value"])
        return value.strip() if value and value.strip() else None
    finally:
        connection.close()


def activate_cursor() -> None:
    result = subprocess.run(
        ["open", "-a", "Cursor"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "no output"
        raise RuntimeError(f"Cursor activation failed: {detail}")


def frontmost_application() -> str | None:
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
    return result.stdout.strip() or None if result.returncode == 0 else None


def send_new_agent_sequence() -> None:
    application_services = ctypes.CDLL(
        "/System/Library/Frameworks/ApplicationServices.framework/"
        "ApplicationServices"
    )
    core_foundation = ctypes.CDLL(
        "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
    )
    create_event = application_services.CGEventCreateKeyboardEvent
    create_event.argtypes = [ctypes.c_void_p, ctypes.c_uint16, ctypes.c_bool]
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
            raise RuntimeError("macOS failed to create a keyboard event")
        try:
            set_flags(event, flags)
            post_event(0, event)
        finally:
            release(event)

    command_key = 55
    option_key = 58
    n_key = 45
    command_flag = 1 << 20
    option_flag = 1 << 19

    post_key(option_key, True, option_flag)
    post_key(command_key, True, option_flag | command_flag)
    try:
        post_key(n_key, True, option_flag | command_flag)
        time.sleep(0.1)
        post_key(n_key, False, option_flag | command_flag)
    finally:
        post_key(command_key, False, option_flag)
        post_key(option_key, False, 0)

    time.sleep(0.5)
    if frontmost_application() != "Cursor":
        raise RuntimeError("Cursor lost foreground before New Agent")

    post_key(command_key, True, command_flag)
    try:
        post_key(n_key, True, command_flag)
        time.sleep(0.1)
        post_key(n_key, False, command_flag)
    finally:
        post_key(command_key, False, 0)


def observe_launch(
    database: Path,
    before_ids: set[str],
    selected_before: str | None,
    timeout: float,
    settle: float,
    started: float,
) -> LaunchObservation:
    deadline = time.monotonic() + timeout
    new_ids: set[str] = set()
    while time.monotonic() < deadline:
        new_ids = set(read_composers(database)) - before_ids
        if new_ids:
            time.sleep(settle)
            new_ids = set(read_composers(database)) - before_ids
            break
        time.sleep(0.1)

    selected_after = read_selected_id(database)
    elapsed_ms = round((time.monotonic() - started) * 1_000)
    ordered_ids = tuple(sorted(new_ids))
    if not new_ids:
        return LaunchObservation(
            "NO_NEW_COMPOSER",
            "No new top-level composer appeared before timeout.",
            True,
            selected_before,
            selected_after,
            ordered_ids,
            elapsed_ms,
        )
    if len(new_ids) != 1:
        return LaunchObservation(
            "AMBIGUOUS_MULTIPLE_COMPOSERS",
            "More than one new top-level composer appeared.",
            True,
            selected_before,
            selected_after,
            ordered_ids,
            elapsed_ms,
        )
    new_id = next(iter(new_ids))
    composer = read_composers(database)[new_id]
    if composer.archived:
        return LaunchObservation(
            "NEW_COMPOSER_ARCHIVED",
            "The new composer is already archived.",
            True,
            selected_before,
            selected_after,
            ordered_ids,
            elapsed_ms,
        )
    if selected_after != new_id:
        return LaunchObservation(
            "NEW_COMPOSER_NOT_SELECTED",
            "Cursor selected a different composer after launch.",
            True,
            selected_before,
            selected_after,
            ordered_ids,
            elapsed_ms,
        )
    return LaunchObservation(
        "LAUNCH_VERIFIED",
        "Exactly one new top-level composer was created and selected.",
        True,
        selected_before,
        selected_after,
        ordered_ids,
        elapsed_ms,
    )


def parse_object(value: Any) -> dict[str, Any]:
    text = decode_text(value)
    if text is None:
        return {}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def decode_text(value: Any) -> str | None:
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return None
    return value if isinstance(value, str) else None


def print_result(result: LaunchObservation, as_json: bool) -> None:
    if as_json:
        print(json.dumps(asdict(result), indent=2, sort_keys=True))
        return
    print("Cursor new-session POC M0.8")
    print(f"Verdict: {result.verdict}")
    print(f"Executed: {result.executed}")
    print(f"Selected before: {result.selected_before}")
    print(f"Selected after: {result.selected_after}")
    print(f"New composer IDs: {list(result.new_composer_ids)}")
    print(f"Elapsed: {result.elapsed_ms} ms")
    print(f"Message: {result.message}")
    print("Safety: read-only SQLite, one Cmd+N maximum, no automatic retry")


def main() -> int:
    args = parse_args()
    if args.timeout <= 0 or args.settle < 0:
        raise SystemExit("--timeout must be positive and --settle non-negative")
    if args.execute and args.confirm != CONFIRMATION:
        raise SystemExit(
            f"--execute requires --confirm {CONFIRMATION}"
        )

    started = time.monotonic()
    before = read_composers(args.database)
    selected_before = read_selected_id(args.database)
    if not args.execute:
        result = LaunchObservation(
            "DRY_RUN",
            "Preflight succeeded; no keyboard event was sent.",
            False,
            selected_before,
            selected_before,
            (),
            round((time.monotonic() - started) * 1_000),
        )
        print_result(result, args.json)
        return 0
    if sys.platform != "darwin":
        raise SystemExit("keyboard execution is supported only on macOS")

    activate_cursor()
    time.sleep(0.25)
    if frontmost_application() != "Cursor":
        raise SystemExit("Cursor is not frontmost; no shortcut was sent")
    send_new_agent_sequence()
    result = observe_launch(
        args.database,
        set(before),
        selected_before,
        args.timeout,
        args.settle,
        started,
    )
    print_result(result, args.json)
    return 0 if result.verdict == "LAUNCH_VERIFIED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
