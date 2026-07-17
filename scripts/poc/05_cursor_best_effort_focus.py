#!/usr/bin/env python3
"""POC M0.5: focus a Cursor session and verify the exact target.

Purpose
=======
Test the strongest safe focus behavior currently available for native local
Cursor sessions on macOS.

Reconnaissance found no supported deep link or CLI argument for opening a local
agent by composer ID. Cursor exposes an agent switcher through ``Control+Tab``
and persists the same recently viewed order in composer header ``recency``.

Method
======
The POC:

1. validates the target composer and resolves its workspace;
2. reads the selected composer before acting;
3. snapshots eligible sessions in descending ``recency`` order;
4. performs no UI action unless ``--execute`` is supplied;
5. activates Cursor and refreshes selection plus recency;
6. verifies the snapshot again immediately before keyboard injection;
7. holds Control and sends one deliberate Tab press per target rank;
8. verifies the exact selected composer and foreground application.

The POC aborts without sending keys if selection or recency changes between the
refresh and final preflight check.

Safety
======
Cursor's database is opened with SQLite ``mode=ro`` and
``PRAGMA query_only=ON``. The default is a dry run. Execution never changes a
workspace, writes the database, sends a prompt, clicks UI coordinates, or
dispatches an agent action. Keyboard presses are bounded by the validated MRU
snapshot. Success requires post-action verification of the exact composer ID.

Examples
========
    python scripts/poc/05_cursor_best_effort_focus.py
    python scripts/poc/05_cursor_best_effort_focus.py --target <composer-id>
    python scripts/poc/05_cursor_best_effort_focus.py --target <id> --execute
    python scripts/poc/05_cursor_best_effort_focus.py --target <id> --json

Interpretation
==============
``FOCUS_VERIFIED`` means the exact target became selected and Cursor was the
foreground application. ``TARGET_SELECTED_CURSOR_NOT_FOREGROUND`` means agent
selection was verified but application focus was not. ``FOCUS_UNVERIFIED``
means the requested ID was not selected before timeout.

``STALE_MRU_SNAPSHOT`` means selection or order changed before key injection,
so the POC sent no keys. ``UNSUPPORTED_SESSION_SWITCH`` means the target is
absent from the validated local MRU snapshot.

Observed on July 16, 2026
=========================
Activating Cursor while the requested composer was already selected produced
``FOCUS_VERIFIED``. Trying ``cursor --reuse-window`` for another session showed
a dialog warning that five running agents would be cancelled by the workspace
change. The test was cancelled, the requested composer was not selected, and
that strategy was removed. An initial MRU round trip appeared to select the
expected targets, while later synthetic events were delivered inconsistently.
Instrumented runs showed that the persisted recency snapshots remained
unchanged during switching. A physical Control+Tab selected persisted rank 1.
After synthetic Tab events were changed from instantaneous events to deliberate
100 ms key presses with pauses, two presses selected and verified the exact
persisted rank-2 target. The table must be refreshed immediately before every
attempt.

Limitations
===========
Composer IDs, ``recency``, and DB keys are undocumented. macOS foreground
detection reports the application, not keyboard focus inside the agent input.
Keyboard injection requires Accessibility permission. Revalidate after Cursor
upgrades.
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
from urllib.parse import unquote, urlparse


DEFAULT_CURSOR_ROOT = Path.home() / "Library/Application Support/Cursor/User"
DEFAULT_DATABASE = DEFAULT_CURSOR_ROOT / "globalStorage/state.vscdb"
DEFAULT_WORKSPACE_STORAGE = DEFAULT_CURSOR_ROOT / "workspaceStorage"
SELECTED_AGENT_KEY = "cursor/glass.selectedAgent"
REQUIRED_TABLES = {"ItemTable", "composerHeaders"}


@dataclass(frozen=True)
class SessionTarget:
    composer_id: str
    workspace_id: str
    workspace_path: str | None
    name: str | None
    archived: bool
    draft: bool
    ephemeral: bool
    subagent: bool


@dataclass(frozen=True)
class FocusResult:
    target: SessionTarget
    selected_before: str | None
    selected_after: str | None
    selected_workspace_before: str | None
    strategy: str
    mru_rank: int | None
    cycle_steps: int
    mru_before: tuple[tuple[str, int], ...]
    mru_after: tuple[tuple[str, int], ...]
    executed: bool
    cursor_frontmost: bool | None
    elapsed_ms: int
    verdict: str
    reasons: tuple[str, ...]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Focus and verify one Cursor local agent session.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Dry-run is the default. --execute may activate Cursor and cycle its\n"
            "agent MRU switcher, but exact DB verification is mandatory."
        ),
    )
    parser.add_argument(
        "--target",
        help="Target composer ID (default: currently selected composer).",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=DEFAULT_DATABASE,
        help=f"Cursor state.vscdb path (default: {DEFAULT_DATABASE})",
    )
    parser.add_argument(
        "--workspace-storage",
        type=Path,
        default=DEFAULT_WORKSPACE_STORAGE,
        help="Cursor workspaceStorage directory.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Perform the proposed focus action and verify its result.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="Verification timeout in seconds (default: 5).",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=0.1,
        help="Verification polling interval in seconds (default: 0.1).",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON output.")
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if args.interval <= 0:
        parser.error("--interval must be positive")
    return args


def connect_read_only(database: Path) -> sqlite3.Connection:
    if not database.is_file():
        raise FileNotFoundError(f"Cursor database not found: {database}")
    connection = sqlite3.connect(
        f"{database.resolve().as_uri()}?mode=ro",
        uri=True,
        timeout=2,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def validate_schema(connection: sqlite3.Connection) -> None:
    tables = {
        row["name"]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    missing = REQUIRED_TABLES - tables
    if missing:
        raise RuntimeError(
            f"Unsupported Cursor schema; missing tables: {sorted(missing)}"
        )


def decode_text(value: Any) -> str | None:
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return None
    return value if isinstance(value, str) else None


def parse_object(value: Any) -> dict[str, Any]:
    text = decode_text(value)
    if text is None:
        return {}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def read_selected_id(connection: sqlite3.Connection) -> str | None:
    row = connection.execute(
        "SELECT value FROM ItemTable WHERE key = ?",
        (SELECTED_AGENT_KEY,),
    ).fetchone()
    if row is None:
        return None
    value = decode_text(row["value"])
    return value.strip() if value and value.strip() else None


def read_workspace_id(
    connection: sqlite3.Connection,
    composer_id: str | None,
) -> str | None:
    if composer_id is None:
        return None
    row = connection.execute(
        "SELECT workspaceId FROM composerHeaders WHERE composerId = ?",
        (composer_id,),
    ).fetchone()
    if row is None:
        return None
    workspace_id = row["workspaceId"]
    return str(workspace_id) if workspace_id else None


def file_uri_to_path(uri: str) -> str | None:
    parsed = urlparse(uri)
    return unquote(parsed.path) if parsed.scheme == "file" else None


def load_workspace_paths(workspace_storage: Path) -> dict[str, str]:
    paths: dict[str, str] = {}
    if not workspace_storage.is_dir():
        return paths
    for workspace_file in workspace_storage.glob("*/workspace.json"):
        try:
            payload = json.loads(workspace_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        folder = payload.get("folder")
        if isinstance(folder, str):
            path = file_uri_to_path(folder)
            if path:
                paths[workspace_file.parent.name] = path
    return paths


def embedded_workspace_path(header: dict[str, Any]) -> str | None:
    candidates = [
        header.get("agentLocation", {}).get("environment", {}).get("uri", {}),
        header.get("workspaceIdentifier", {}).get("uri", {}),
    ]
    for uri in candidates:
        if not isinstance(uri, dict):
            continue
        path = uri.get("fsPath") or uri.get("path")
        if isinstance(path, str) and path:
            return path
        external = uri.get("external")
        if isinstance(external, str):
            path = file_uri_to_path(external)
            if path:
                return path
    return None


def read_target(
    connection: sqlite3.Connection,
    composer_id: str,
    workspace_paths: dict[str, str],
) -> SessionTarget:
    row = connection.execute(
        """
        SELECT composerId, workspaceId, isArchived, isSubagent, value
        FROM composerHeaders
        WHERE composerId = ?
        """,
        (composer_id,),
    ).fetchone()
    if row is None:
        raise RuntimeError(f"Target composer not found: {composer_id}")
    header = parse_object(row["value"])
    workspace_id = str(row["workspaceId"] or "")
    return SessionTarget(
        composer_id=str(row["composerId"]),
        workspace_id=workspace_id,
        workspace_path=(
            embedded_workspace_path(header) or workspace_paths.get(workspace_id)
        ),
        name=string_or_none(header.get("name")),
        archived=bool(row["isArchived"]),
        draft=bool(header.get("isDraft", False)),
        ephemeral=bool(header.get("isEphemeral", False)),
        subagent=bool(row["isSubagent"]),
    )


def string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def validate_target(target: SessionTarget) -> tuple[str, ...]:
    reasons: list[str] = []
    if target.archived:
        reasons.append("Target is archived.")
    if target.draft:
        reasons.append("Target is a draft.")
    if target.ephemeral:
        reasons.append("Target is ephemeral.")
    if target.subagent:
        reasons.append("Target is a subagent.")
    if not target.workspace_id:
        reasons.append("Target has no workspace ID.")
    if not target.workspace_path:
        reasons.append("Target workspace path could not be resolved.")
    elif not Path(target.workspace_path).is_dir():
        reasons.append("Target workspace path is not an existing directory.")
    return tuple(reasons)


def read_mru_snapshot(
    connection: sqlite3.Connection,
) -> list[tuple[str, int]]:
    membership_row = connection.execute(
        "SELECT value FROM ItemTable WHERE key = ?",
        ("glass.localAgentProjectMembership.v1",),
    ).fetchone()
    if membership_row is None:
        raise RuntimeError("Cursor local-agent project membership is absent.")
    memberships = parse_object(membership_row["value"])

    candidates: list[tuple[int, str]] = []
    rows = connection.execute(
        """
        SELECT composerId, lastUpdatedAt, recency, isArchived, isSubagent, value
        FROM composerHeaders
        """
    )
    for row in rows:
        composer_id = str(row["composerId"])
        if composer_id not in memberships:
            continue
        if bool(row["isArchived"]) or bool(row["isSubagent"]):
            continue
        header = parse_object(row["value"])
        if bool(header.get("isDraft", False)):
            continue
        if bool(header.get("isEphemeral", False)):
            continue
        recency = row["recency"]
        updated_at = row["lastUpdatedAt"]
        rank_value = (
            recency
            if isinstance(recency, int)
            else updated_at if isinstance(updated_at, int) else None
        )
        if rank_value is None:
            continue
        candidates.append((rank_value, composer_id))
    candidates.sort(reverse=True)
    return [
        (composer_id, recency)
        for recency, composer_id in candidates
    ]


def choose_strategy(
    target: SessionTarget,
    selected_before: str | None,
    mru_order: list[str],
) -> tuple[str, int | None]:
    if selected_before == target.composer_id:
        return "activate_application", 0
    if selected_before is None or selected_before not in mru_order:
        return "none_selected_absent_from_mru", None
    if target.composer_id not in mru_order:
        return "none_target_absent_from_mru", None
    ordered = [
        selected_before,
        *(
            composer_id
            for composer_id in mru_order
            if composer_id != selected_before
        ),
    ]
    return "cycle_agent_mru", ordered.index(target.composer_id)


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
        raise RuntimeError(
            f"Focus command failed with exit {result.returncode}: {detail}"
        )


def cycle_agent_mru(steps: int) -> None:
    if steps <= 0:
        raise RuntimeError("MRU cycle steps must be positive.")
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
            raise RuntimeError("macOS failed to create a keyboard event.")
        try:
            set_flags(event, flags)
            post_event(0, event)
        finally:
            release(event)

    control_key = 59
    tab_key = 48
    control_flag = 1 << 18
    post_key(control_key, True, control_flag)
    try:
        for index in range(steps):
            post_key(tab_key, True, control_flag)
            time.sleep(0.1)
            post_key(tab_key, False, control_flag)
            time.sleep(0.7 if index == 0 else 0.4)
        time.sleep(0.5)
    finally:
        post_key(control_key, False, 0)
    time.sleep(0.5)


def read_frontmost_application() -> str | None:
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


def verify_focus(
    connection: sqlite3.Connection,
    target_id: str,
    timeout: float,
    interval: float,
) -> tuple[str | None, bool | None]:
    deadline = time.monotonic() + timeout
    selected = read_selected_id(connection)
    frontmost_name = read_frontmost_application()
    while (
        (selected != target_id or frontmost_name != "Cursor")
        and time.monotonic() < deadline
    ):
        time.sleep(min(interval, max(0, deadline - time.monotonic())))
        selected = read_selected_id(connection)
        frontmost_name = read_frontmost_application()
    return selected, (
        frontmost_name == "Cursor" if frontmost_name is not None else None
    )


def inspect_or_focus(
    connection: sqlite3.Connection,
    args: argparse.Namespace,
) -> FocusResult:
    started = time.monotonic()
    selected_before = read_selected_id(connection)
    target_id = args.target or selected_before
    if target_id is None:
        raise RuntimeError("No --target supplied and Cursor has no selected agent.")

    workspace_paths = load_workspace_paths(args.workspace_storage)
    target = read_target(connection, target_id, workspace_paths)
    target_reasons = validate_target(target)
    selected_workspace_before = read_workspace_id(connection, selected_before)
    mru_before = tuple(read_mru_snapshot(connection))
    mru_order = [composer_id for composer_id, _ in mru_before]
    strategy, mru_rank = choose_strategy(
        target,
        selected_before,
        mru_order,
    )

    if target_reasons:
        return FocusResult(
            target=target,
            selected_before=selected_before,
            selected_after=selected_before,
            selected_workspace_before=selected_workspace_before,
            strategy="none_invalid_target",
            mru_rank=None,
            cycle_steps=0,
            mru_before=mru_before,
            mru_after=mru_before,
            executed=False,
            cursor_frontmost=None,
            elapsed_ms=elapsed_ms(started),
            verdict="INVALID_TARGET",
            reasons=target_reasons,
        )

    if not args.execute:
        if strategy.startswith("none_"):
            return FocusResult(
                target=target,
                selected_before=selected_before,
                selected_after=selected_before,
                selected_workspace_before=selected_workspace_before,
                strategy=strategy,
                mru_rank=None,
                cycle_steps=0,
                mru_before=mru_before,
                mru_after=mru_before,
                executed=False,
                cursor_frontmost=None,
                elapsed_ms=elapsed_ms(started),
                verdict="UNSUPPORTED_SESSION_SWITCH",
                reasons=(
                    "The current and target sessions are not both addressable "
                    "in the validated MRU snapshot.",
                ),
            )
        verdict = (
            "DRY_RUN_ALREADY_SELECTED"
            if selected_before == target.composer_id
            else "READY"
        )
        return FocusResult(
            target=target,
            selected_before=selected_before,
            selected_after=selected_before,
            selected_workspace_before=selected_workspace_before,
            strategy=strategy,
            mru_rank=mru_rank,
            cycle_steps=(
                mru_rank
                if strategy == "cycle_agent_mru"
                and mru_rank is not None
                else 0
            ),
            mru_before=mru_before,
            mru_after=mru_before,
            executed=False,
            cursor_frontmost=None,
            elapsed_ms=elapsed_ms(started),
            verdict=verdict,
            reasons=(
                "Run with --execute to activate Cursor, cycle the MRU, and verify.",
            ),
        )

    activate_cursor()
    time.sleep(0.2)
    selected_before = read_selected_id(connection)
    selected_workspace_before = read_workspace_id(connection, selected_before)
    mru_before = tuple(read_mru_snapshot(connection))
    mru_order = [composer_id for composer_id, _ in mru_before]
    strategy, mru_rank = choose_strategy(
        target,
        selected_before,
        mru_order,
    )
    if strategy.startswith("none_"):
        return FocusResult(
            target=target,
            selected_before=selected_before,
            selected_after=selected_before,
            selected_workspace_before=selected_workspace_before,
            strategy=strategy,
            mru_rank=None,
            cycle_steps=0,
            mru_before=mru_before,
            mru_after=mru_before,
            executed=True,
            cursor_frontmost=read_frontmost_application() == "Cursor",
            elapsed_ms=elapsed_ms(started),
            verdict="UNSUPPORTED_SESSION_SWITCH",
            reasons=(
                "The current and target sessions are not both addressable "
                "in the validated MRU snapshot after activation.",
            ),
        )

    cycle_steps = (
        mru_rank
        if strategy == "cycle_agent_mru" and mru_rank is not None
        else 0
    )
    if strategy == "cycle_agent_mru":
        latest_selected = read_selected_id(connection)
        latest_mru = tuple(read_mru_snapshot(connection))
        if latest_selected != selected_before or latest_mru != mru_before:
            return FocusResult(
                target=target,
                selected_before=selected_before,
                selected_after=latest_selected,
                selected_workspace_before=selected_workspace_before,
                strategy="none_stale_mru_snapshot",
                mru_rank=mru_rank,
                cycle_steps=0,
                mru_before=mru_before,
                mru_after=latest_mru,
                executed=True,
                cursor_frontmost=read_frontmost_application() == "Cursor",
                elapsed_ms=elapsed_ms(started),
                verdict="STALE_MRU_SNAPSHOT",
                reasons=(
                    "Selection or recency changed before keyboard injection.",
                    "No keyboard event was sent.",
                ),
            )
        cycle_agent_mru(cycle_steps)
    selected_after, cursor_frontmost = verify_focus(
        connection,
        target.composer_id,
        args.timeout,
        args.interval,
    )
    mru_after = tuple(read_mru_snapshot(connection))
    if selected_after != target.composer_id:
        verdict = "FOCUS_UNVERIFIED"
        reasons = (
            "The exact target composer was not selected before timeout.",
            "No session action is safe after this result.",
        )
    elif cursor_frontmost is True:
        verdict = "FOCUS_VERIFIED"
        reasons = (
            "The exact target composer is selected.",
            "Cursor is the foreground macOS application.",
        )
    else:
        verdict = "TARGET_SELECTED_CURSOR_NOT_FOREGROUND"
        reasons = (
            "The exact target composer is selected.",
            "Cursor foreground application status could not be verified.",
        )
    return FocusResult(
        target=target,
        selected_before=selected_before,
        selected_after=selected_after,
        selected_workspace_before=selected_workspace_before,
        strategy=strategy,
        mru_rank=mru_rank,
        cycle_steps=cycle_steps,
        mru_before=mru_before,
        mru_after=mru_after,
        executed=True,
        cursor_frontmost=cursor_frontmost,
        elapsed_ms=elapsed_ms(started),
        verdict=verdict,
        reasons=reasons,
    )


def elapsed_ms(started: float) -> int:
    return round((time.monotonic() - started) * 1000)


def print_human(
    result: FocusResult,
    database: Path,
    query_only: bool,
) -> None:
    print("Cursor best-effort focus POC M0.5")
    print(f"Database: {database}")
    print(f"Safety: mode=ro, query_only={str(query_only).lower()}")
    print(f"Target composer: {result.target.composer_id}")
    print(f"Target workspace: {result.target.workspace_path!r}")
    print(f"Selected before: {result.selected_before!r}")
    print(f"Selected after: {result.selected_after!r}")
    print(f"Strategy: {result.strategy}")
    print(f"MRU rank: {result.mru_rank!r}")
    print(f"Cycle steps: {result.cycle_steps}")
    print("MRU before:")
    for index, (composer_id, recency) in enumerate(result.mru_before):
        print(f"  {index}: {composer_id[:8]}… recency={recency}")
    print("MRU after:")
    for index, (composer_id, recency) in enumerate(result.mru_after):
        print(f"  {index}: {composer_id[:8]}… recency={recency}")
    print(f"Executed: {str(result.executed).lower()}")
    print(f"Cursor frontmost: {result.cursor_frontmost!r}")
    print(f"Elapsed: {result.elapsed_ms}ms")
    print()
    print(f"VERDICT: {result.verdict}")
    for reason in result.reasons:
        print(f"  - {reason}")


def print_json(
    result: FocusResult,
    database: Path,
    query_only: bool,
) -> None:
    payload = asdict(result)
    payload["database"] = str(database)
    payload["query_only"] = query_only
    print(json.dumps(payload, indent=2, sort_keys=True))


def main() -> int:
    args = parse_args()
    try:
        with connect_read_only(args.database) as connection:
            validate_schema(connection)
            query_only = bool(connection.execute("PRAGMA query_only").fetchone()[0])
            result = inspect_or_focus(connection, args)
            if args.json:
                print_json(result, args.database, query_only)
            else:
                print_human(result, args.database, query_only)
            return 1 if result.verdict == "FOCUS_UNVERIFIED" else 0
    except (
        FileNotFoundError,
        RuntimeError,
        sqlite3.Error,
        subprocess.SubprocessError,
    ) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
