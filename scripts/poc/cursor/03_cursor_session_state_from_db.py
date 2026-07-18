#!/usr/bin/env python3
"""POC M0.3: observe Cursor session execution state using only its database.

Purpose
=======
Determine whether Cursor's local ``state.vscdb`` is sufficient to monitor agent
execution without hooks. The POC reads the selected agent, its ``composerHeaders``
row, and its exact ``cursorDiskKV`` record:

``composerData:<composer-id>``

It prints state-relevant fields and their transitions while deliberately
redacting prompts, conversation content, file contents, and encryption keys.

This POC observes raw evidence before defining elChango's final state machine.
In particular, a persisted ``completed`` or ``aborted`` value may describe the
last turn rather than the session's current activity.

Safety
======
The database is opened with SQLite ``mode=ro`` and ``PRAGMA query_only=ON``.
Only exact indexed keys are queried. The POC uses the Python standard library,
does not install hooks, and does not modify Cursor.

Examples
========
    python scripts/poc/cursor/03_cursor_session_state_from_db.py
    python scripts/poc/cursor/03_cursor_session_state_from_db.py --json
    python scripts/poc/cursor/03_cursor_session_state_from_db.py --watch 90
    python scripts/poc/cursor/03_cursor_session_state_from_db.py --watch 90 --interval 0.1
    python scripts/poc/cursor/03_cursor_session_state_from_db.py --composer-id <uuid>

Test protocol
=============
During ``--watch``:

1. submit a prompt;
2. let the agent call tools;
3. trigger a confirmation if practical;
4. let the turn complete;
5. run a second observation that is cancelled or errors.

Decision rule
=============
If database transitions distinguish running, waiting, completion, cancellation,
and error with acceptable latency, hooks are unnecessary. If only specific
transitions are missing, hooks may complement the database. If the database
does not expose usable transitions, hooks become necessary.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_DATABASE = (
    Path.home()
    / "Library/Application Support/Cursor/User/globalStorage/state.vscdb"
)
SELECTED_AGENT_KEY = "cursor/glass.selectedAgent"
COMPOSER_KEY_PREFIX = "composerData:"
REQUIRED_TABLES = {"ItemTable", "composerHeaders", "cursorDiskKV"}


@dataclass(frozen=True)
class StateSnapshot:
    """Redacted state signals for one composer at one instant."""

    composer_id: str
    observed_at_ms: int
    raw_status: str | None
    inferred_phase: str
    generating_bubble_count: int
    queue_item_count: int
    continuation_in_progress: bool
    blocking_pending_actions: bool
    pending_plan: bool
    reading_long_file: bool
    creating_worktree: bool
    applying_worktree: bool
    undoing_worktree: bool
    unread_messages: bool
    stop_hook_loop_count: int | None
    header_updated_at_ms: int | None
    data_updated_at_ms: int | None
    checkpoint_updated_at_ms: int | None
    conversation_header_count: int
    conversation_state_bytes: int
    conversation_state_digest: str | None
    latest_generation_id: str | None
    verdict: str
    limitations: tuple[str, ...]

    def signal_values(self) -> dict[str, Any]:
        """Return fields whose deltas are safe and useful to print."""

        ignored = {
            "composer_id",
            "observed_at_ms",
            "verdict",
            "limitations",
        }
        return {
            key: value
            for key, value in asdict(self).items()
            if key not in ignored
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Observe Cursor execution-state transitions from state.vscdb only.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "The POC prints only redacted state metadata. It never prints prompts,\n"
            "conversation text, file contents, or encryption keys."
        ),
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=DEFAULT_DATABASE,
        help=f"Cursor state.vscdb path (default: {DEFAULT_DATABASE})",
    )
    parser.add_argument(
        "--composer-id",
        help="Observe this composer instead of Cursor's selected agent.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON output.")
    parser.add_argument(
        "--watch",
        type=float,
        metavar="SECONDS",
        help="Watch state transitions for this duration.",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=0.2,
        help="Polling interval used with --watch (default: 0.2 seconds).",
    )
    args = parser.parse_args()
    if args.watch is not None and args.watch <= 0:
        parser.error("--watch must be positive")
    if args.interval <= 0:
        parser.error("--interval must be positive")
    if args.json and args.watch:
        parser.error("--json and --watch cannot be combined")
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


def parse_json(value: Any) -> dict[str, Any]:
    text = decode_text(value)
    if text is None:
        return {}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def read_selected_id(connection: sqlite3.Connection) -> str:
    row = connection.execute(
        "SELECT value FROM ItemTable WHERE key = ?",
        (SELECTED_AGENT_KEY,),
    ).fetchone()
    if row is None:
        raise RuntimeError(f"ItemTable key {SELECTED_AGENT_KEY!r} is absent.")
    composer_id = decode_text(row["value"])
    if not composer_id or not composer_id.strip():
        raise RuntimeError(f"ItemTable key {SELECTED_AGENT_KEY!r} is empty.")
    return composer_id.strip()


def read_snapshot(
    connection: sqlite3.Connection,
    composer_id: str,
) -> StateSnapshot:
    header_row = connection.execute(
        """
        SELECT lastUpdatedAt, value
        FROM composerHeaders
        WHERE composerId = ?
        """,
        (composer_id,),
    ).fetchone()
    if header_row is None:
        raise RuntimeError(f"Composer header not found: {composer_id}")

    data_row = connection.execute(
        "SELECT value FROM cursorDiskKV WHERE key = ?",
        (f"{COMPOSER_KEY_PREFIX}{composer_id}",),
    ).fetchone()
    if data_row is None:
        raise RuntimeError(f"Composer data not found: {composer_id}")

    header = parse_json(header_row["value"])
    data = parse_json(data_row["value"])
    if not data:
        raise RuntimeError(f"Composer data is not valid JSON: {composer_id}")

    raw_status = data.get("status")
    raw_status = raw_status if isinstance(raw_status, str) else None
    generating_count = list_length(data.get("generatingBubbleIds"))
    queue_count = list_length(data.get("queueItems"))
    continuation = bool(data.get("isContinuationInProgress", False))
    blocking = bool(
        data.get("hasBlockingPendingActions", False)
        or header.get("hasBlockingPendingActions", False)
    )
    conversation_state = data.get("conversationState")
    if isinstance(conversation_state, str):
        conversation_bytes = len(conversation_state.encode("utf-8"))
        conversation_digest = hashlib.sha256(
            conversation_state.encode("utf-8")
        ).hexdigest()[:12]
    else:
        conversation_bytes = 0
        conversation_digest = None

    return StateSnapshot(
        composer_id=composer_id,
        observed_at_ms=time.time_ns() // 1_000_000,
        raw_status=raw_status,
        inferred_phase=infer_phase(
            raw_status=raw_status,
            generating_count=generating_count,
            queue_count=queue_count,
            continuation=continuation,
            blocking=blocking,
        ),
        generating_bubble_count=generating_count,
        queue_item_count=queue_count,
        continuation_in_progress=continuation,
        blocking_pending_actions=blocking,
        pending_plan=bool(
            data.get("hasPendingPlan", False)
            or header.get("hasPendingPlan", False)
        ),
        reading_long_file=bool(data.get("isReadingLongFile", False)),
        creating_worktree=bool(data.get("isCreatingWorktree", False)),
        applying_worktree=bool(data.get("isApplyingWorktree", False)),
        undoing_worktree=bool(data.get("isUndoingWorktree", False)),
        unread_messages=bool(data.get("hasUnreadMessages", False)),
        stop_hook_loop_count=as_int(data.get("stopHookLoopCount")),
        header_updated_at_ms=as_int(header_row["lastUpdatedAt"]),
        data_updated_at_ms=as_int(data.get("lastUpdatedAt")),
        checkpoint_updated_at_ms=as_int(
            data.get("conversationCheckpointLastUpdatedAt")
        ),
        conversation_header_count=list_length(
            data.get("fullConversationHeadersOnly")
        ),
        conversation_state_bytes=conversation_bytes,
        conversation_state_digest=conversation_digest,
        latest_generation_id=string_or_none(
            data.get("latestChatGenerationUUID")
            or data.get("chatGenerationUUID")
        ),
        verdict="SIGNALS_FOUND",
        limitations=(
            "Raw status may describe the last turn rather than current activity.",
            "The inferred phase is provisional until live transitions are observed.",
            "Conversation content is intentionally redacted.",
            "All fields are undocumented Cursor internals.",
        ),
    )


def list_length(value: Any) -> int:
    return len(value) if isinstance(value, list) else 0


def as_int(value: Any) -> int | None:
    return value if isinstance(value, int) else None


def string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def infer_phase(
    *,
    raw_status: str | None,
    generating_count: int,
    queue_count: int,
    continuation: bool,
    blocking: bool,
) -> str:
    """Return a conservative provisional phase from observed fields."""

    if blocking:
        return "waiting_input_candidate"
    if generating_count or continuation:
        return "running_candidate"
    if queue_count:
        return "queued_candidate"
    if raw_status in {"error", "failed"}:
        return "error_candidate"
    if raw_status == "aborted":
        return "aborted_last_turn"
    if raw_status == "completed":
        return "completed_last_turn"
    if raw_status in {"generating", "running", "pending"}:
        return "running_candidate"
    return "unknown"


def format_time(timestamp_ms: int | None) -> str:
    if timestamp_ms is None:
        return "unknown"
    return datetime.fromtimestamp(timestamp_ms / 1000).astimezone().isoformat(
        timespec="milliseconds"
    )


def format_value(key: str, value: Any) -> str:
    if key.endswith("_at_ms"):
        return format_time(value)
    return repr(value)


def print_human(
    snapshot: StateSnapshot,
    database: Path,
    query_only: bool,
) -> None:
    print("Cursor DB-only session-state POC M0.3")
    print(f"Database: {database}")
    print(f"Safety: mode=ro, query_only={str(query_only).lower()}, hooks=false")
    print(f"Composer: {snapshot.composer_id}")
    print()
    for key, value in snapshot.signal_values().items():
        print(f"{key}: {format_value(key, value)}")
    print()
    print(f"VERDICT: {snapshot.verdict}")
    for limitation in snapshot.limitations:
        print(f"  - {limitation}")


def print_json(
    snapshot: StateSnapshot,
    database: Path,
    query_only: bool,
) -> None:
    payload = asdict(snapshot)
    payload["database"] = str(database)
    payload["query_only"] = query_only
    payload["hooks"] = False
    print(json.dumps(payload, indent=2, sort_keys=True))


def changed_signals(
    previous: StateSnapshot,
    current: StateSnapshot,
) -> dict[str, tuple[Any, Any]]:
    old = previous.signal_values()
    new = current.signal_values()
    return {
        key: (old.get(key), new.get(key))
        for key in new
        if old.get(key) != new.get(key)
    }


def watch_state(
    connection: sqlite3.Connection,
    args: argparse.Namespace,
    initial_composer_id: str,
    initial: StateSnapshot,
) -> None:
    previous = initial
    composer_id = initial_composer_id
    deadline = time.monotonic() + args.watch
    print()
    print(f"Watching for {args.watch:g}s every {args.interval:g}s. Press Ctrl-C to stop.")
    try:
        while time.monotonic() < deadline:
            time.sleep(min(args.interval, max(0, deadline - time.monotonic())))
            if args.composer_id is None:
                selected_id = read_selected_id(connection)
                if selected_id != composer_id:
                    now = datetime.now().astimezone().isoformat(
                        timespec="milliseconds"
                    )
                    print(
                        f"{now} SELECTED {short_id(composer_id)} -> "
                        f"{short_id(selected_id)}"
                    )
                    composer_id = selected_id
                    previous = read_snapshot(connection, composer_id)
                    continue
            current = read_snapshot(connection, composer_id)
            changes = changed_signals(previous, current)
            if not changes:
                continue
            now = datetime.now().astimezone().isoformat(timespec="milliseconds")
            print(
                f"{now} STATE {short_id(composer_id)} "
                f"phase={current.inferred_phase} raw_status={current.raw_status!r}"
            )
            for key, (old, new) in changes.items():
                print(
                    f"  {key}: {format_value(key, old)} -> "
                    f"{format_value(key, new)}"
                )
            previous = current
    except KeyboardInterrupt:
        print("\nWatch stopped.")


def short_id(composer_id: str) -> str:
    return composer_id if len(composer_id) <= 12 else f"{composer_id[:8]}…"


def main() -> int:
    args = parse_args()
    try:
        with connect_read_only(args.database) as connection:
            validate_schema(connection)
            query_only = bool(connection.execute("PRAGMA query_only").fetchone()[0])
            composer_id = args.composer_id or read_selected_id(connection)
            snapshot = read_snapshot(connection, composer_id)
            if args.json:
                print_json(snapshot, args.database, query_only)
            else:
                print_human(snapshot, args.database, query_only)
            if args.watch:
                watch_state(connection, args, composer_id, snapshot)
            return 0
    except (FileNotFoundError, sqlite3.Error, RuntimeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
