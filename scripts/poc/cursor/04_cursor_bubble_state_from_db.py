#!/usr/bin/env python3
"""POC M0.4: observe Cursor execution through per-bubble database records.

Purpose
=======
Test whether Cursor's ``bubbleId:<composer-id>:<bubble-id>`` records expose
enough live state to monitor agent and tool execution without hooks.

POC M0.3 showed that aggregate fields in ``composerData:<composer-id>`` can
remain stale throughout a turn. A follow-up probe found per-bubble fields such
as ``toolFormerData.status=loading`` and later ``completed``. This POC observes
those records systematically.

Method
======
The POC:

1. resolves the selected composer, or uses ``--composer-id``;
2. reads bubble IDs from that composer's ordered header list;
3. queries only the corresponding exact ``bubbleId:...`` keys;
4. reports new bubbles and metadata transitions;
5. redacts prompts, responses, tool arguments, tool results, and file content.

Safety
======
The database is opened with SQLite ``mode=ro`` and ``PRAGMA query_only=ON``.
The POC uses only the Python standard library and does not modify Cursor or
install hooks. Payload hashes and byte counts reveal change without content.

Examples
========
    python scripts/poc/cursor/04_cursor_bubble_state_from_db.py
    python scripts/poc/cursor/04_cursor_bubble_state_from_db.py --json
    python scripts/poc/cursor/04_cursor_bubble_state_from_db.py --watch 120
    python scripts/poc/cursor/04_cursor_bubble_state_from_db.py --watch 120 --interval 0.1
    python scripts/poc/cursor/04_cursor_bubble_state_from_db.py --composer-id <uuid>

Test protocol
=============
During ``--watch``:

1. submit a prompt that causes at least one tool call;
2. observe a tool bubble while it runs and after it completes;
3. if practical, trigger a confirmation and then accept or reject it;
4. run another turn that is cancelled or errors;
5. separately test a response that does not call a tool.

Decision rule
=============
The DB-only approach remains viable if bubble transitions distinguish running,
waiting, completion, cancellation, and error with acceptable latency. Missing
states should be identified before hooks are proposed as a complement.

Observed on July 16, 2026
=========================
A controlled live run detected new tool bubbles with ``status=loading`` and
later observed those same records change to ``status=completed``. Some result
statuses were revised when Cursor persisted the completed turn, so consumers
must treat intermediate values as provisional. Waiting, genuine cancellation,
and error still require dedicated tests.

Limitations
===========
All inspected fields and numeric type codes are undocumented Cursor internals.
A tool's completion may be persisted only when Cursor processes the next event.
Revalidate this POC after Cursor upgrades.
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
BUBBLE_KEY_PREFIX = "bubbleId:"
REQUIRED_TABLES = {"ItemTable", "cursorDiskKV"}


@dataclass(frozen=True)
class BubbleSnapshot:
    """Content-free execution metadata for one Cursor bubble."""

    bubble_id: str
    bubble_type: int | None
    capability_type: int | None
    tool_code: int | None
    tool_status: str | None
    result_status: str | None
    inferred_phase: str
    payload_bytes: int
    payload_digest: str

    def signal_values(self) -> dict[str, Any]:
        """Return fields whose transitions are useful to print."""

        values = asdict(self)
        values.pop("bubble_id")
        return values


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Observe Cursor per-bubble execution state from state.vscdb.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Output contains IDs, type codes, statuses, sizes, and hashes only.\n"
            "Prompts, responses, tool inputs, and tool outputs are never printed."
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
    parser.add_argument(
        "--recent",
        type=int,
        default=20,
        help="Number of recent bubbles to inspect per poll (default: 20).",
    )
    parser.add_argument("--json", action="store_true", help="Emit one JSON snapshot.")
    parser.add_argument(
        "--watch",
        type=float,
        metavar="SECONDS",
        help="Watch bubble transitions for this duration.",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=0.2,
        help="Polling interval used with --watch (default: 0.2 seconds).",
    )
    args = parser.parse_args()
    if args.recent <= 0:
        parser.error("--recent must be positive")
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


def parse_object(value: Any, label: str) -> tuple[dict[str, Any], str]:
    text = decode_text(value)
    if text is None:
        raise RuntimeError(f"{label} is not UTF-8 JSON.")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"{label} is not valid JSON: {error}") from error
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label} is not a JSON object.")
    return payload, text


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


def read_bubble_ids(
    connection: sqlite3.Connection,
    composer_id: str,
) -> list[str]:
    key = f"{COMPOSER_KEY_PREFIX}{composer_id}"
    row = connection.execute(
        "SELECT value FROM cursorDiskKV WHERE key = ?",
        (key,),
    ).fetchone()
    if row is None:
        raise RuntimeError(f"Composer data not found: {composer_id}")
    data, _ = parse_object(row["value"], key)
    headers = data.get("fullConversationHeadersOnly")
    if not isinstance(headers, list):
        raise RuntimeError(
            f"Unsupported Cursor schema; {key} has no bubble header list."
        )

    bubble_ids: list[str] = []
    seen: set[str] = set()
    for header in headers:
        if not isinstance(header, dict):
            raise RuntimeError(
                f"Unsupported Cursor schema; {key} contains a non-object header."
            )
        bubble_id = header.get("bubbleId")
        if not isinstance(bubble_id, str) or not bubble_id:
            raise RuntimeError(
                f"Unsupported Cursor schema; {key} contains an invalid bubble ID."
            )
        if bubble_id not in seen:
            bubble_ids.append(bubble_id)
            seen.add(bubble_id)
    return bubble_ids


def read_bubble(
    connection: sqlite3.Connection,
    composer_id: str,
    bubble_id: str,
) -> BubbleSnapshot:
    key = f"{BUBBLE_KEY_PREFIX}{composer_id}:{bubble_id}"
    row = connection.execute(
        "SELECT value FROM cursorDiskKV WHERE key = ?",
        (key,),
    ).fetchone()
    if row is None:
        raise RuntimeError(f"Referenced bubble record not found: {key}")
    bubble, text = parse_object(row["value"], key)

    tool_data = bubble.get("toolFormerData")
    tool_data = tool_data if isinstance(tool_data, dict) else {}
    additional_data = tool_data.get("additionalData")
    additional_data = (
        additional_data if isinstance(additional_data, dict) else {}
    )
    tool_status = string_or_none(tool_data.get("status"))
    result_status = string_or_none(additional_data.get("status"))

    encoded = text.encode("utf-8")
    return BubbleSnapshot(
        bubble_id=bubble_id,
        bubble_type=integer_or_none(bubble.get("type")),
        capability_type=integer_or_none(bubble.get("capabilityType")),
        tool_code=integer_or_none(tool_data.get("tool")),
        tool_status=tool_status,
        result_status=result_status,
        inferred_phase=infer_phase(tool_status, result_status, bool(tool_data)),
        payload_bytes=len(encoded),
        payload_digest=hashlib.sha256(encoded).hexdigest()[:12],
    )


def read_recent(
    connection: sqlite3.Connection,
    composer_id: str,
    recent: int,
) -> list[BubbleSnapshot]:
    bubble_ids = read_bubble_ids(connection, composer_id)[-recent:]
    return [
        read_bubble(connection, composer_id, bubble_id)
        for bubble_id in bubble_ids
    ]


def integer_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def infer_phase(
    tool_status: str | None,
    result_status: str | None,
    has_tool_data: bool,
) -> str:
    """Return a conservative phase based only on observed status strings."""

    normalized_tool = tool_status.lower() if tool_status else None
    normalized_result = result_status.lower() if result_status else None
    if normalized_result in {"error", "failed", "failure"}:
        return "tool_error_candidate"
    if normalized_result in {"aborted", "cancelled", "canceled"}:
        return "tool_cancelled_candidate"
    if normalized_tool in {"aborted", "cancelled", "canceled"}:
        return "tool_cancelled_candidate"
    if normalized_tool in {"loading", "running", "pending", "in_progress"}:
        return "tool_running_candidate"
    if normalized_tool == "completed" and normalized_result == "success":
        return "tool_succeeded"
    if normalized_tool == "completed":
        return "tool_completed"
    if has_tool_data:
        return "tool_unknown"
    return "non_tool_bubble"


def short_id(identifier: str) -> str:
    return identifier if len(identifier) <= 12 else f"{identifier[:8]}…"


def format_snapshot(snapshot: BubbleSnapshot) -> str:
    return (
        f"phase={snapshot.inferred_phase} "
        f"tool_status={snapshot.tool_status!r} "
        f"result_status={snapshot.result_status!r} "
        f"type={snapshot.bubble_type!r} "
        f"capability={snapshot.capability_type!r} "
        f"tool={snapshot.tool_code!r} "
        f"bytes={snapshot.payload_bytes} "
        f"digest={snapshot.payload_digest}"
    )


def print_human(
    snapshots: list[BubbleSnapshot],
    composer_id: str,
    database: Path,
    query_only: bool,
) -> None:
    print("Cursor DB-only per-bubble state POC M0.4")
    print(f"Database: {database}")
    print(f"Safety: mode=ro, query_only={str(query_only).lower()}, hooks=false")
    print(f"Composer: {composer_id}")
    print(f"Recent bubble records: {len(snapshots)}")
    for snapshot in snapshots:
        print(f"  {short_id(snapshot.bubble_id)} {format_snapshot(snapshot)}")
    print()
    print("VERDICT: SIGNALS_FOUND" if snapshots else "VERDICT: NO_BUBBLES")
    print("  - Status semantics and persistence latency remain to be validated.")
    print("  - Conversation and tool content is intentionally redacted.")
    print("  - All fields are undocumented Cursor internals.")


def print_json(
    snapshots: list[BubbleSnapshot],
    composer_id: str,
    database: Path,
    query_only: bool,
) -> None:
    payload = {
        "composer_id": composer_id,
        "database": str(database),
        "query_only": query_only,
        "hooks": False,
        "bubbles": [asdict(snapshot) for snapshot in snapshots],
        "limitations": [
            "Status semantics and persistence latency remain to be validated.",
            "Conversation and tool content is intentionally redacted.",
            "All fields are undocumented Cursor internals.",
        ],
    }
    print(json.dumps(payload, indent=2, sort_keys=True))


def changed_signals(
    previous: BubbleSnapshot,
    current: BubbleSnapshot,
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
    initial: list[BubbleSnapshot],
) -> None:
    composer_id = initial_composer_id
    previous = {snapshot.bubble_id: snapshot for snapshot in initial}
    deadline = time.monotonic() + args.watch
    print()
    print(f"Watching for {args.watch:g}s every {args.interval:g}s. Press Ctrl-C to stop.")
    try:
        while time.monotonic() < deadline:
            time.sleep(min(args.interval, max(0, deadline - time.monotonic())))
            if args.composer_id is None:
                selected_id = read_selected_id(connection)
                if selected_id != composer_id:
                    now = current_time()
                    print(
                        f"{now} SELECTED {short_id(composer_id)} -> "
                        f"{short_id(selected_id)}"
                    )
                    composer_id = selected_id
                    snapshots = read_recent(connection, composer_id, args.recent)
                    previous = {
                        snapshot.bubble_id: snapshot for snapshot in snapshots
                    }
                    continue

            snapshots = read_recent(connection, composer_id, args.recent)
            current = {snapshot.bubble_id: snapshot for snapshot in snapshots}
            for snapshot in snapshots:
                old = previous.get(snapshot.bubble_id)
                if old is None:
                    print(
                        f"{current_time()} NEW {short_id(snapshot.bubble_id)} "
                        f"{format_snapshot(snapshot)}"
                    )
                    continue
                changes = changed_signals(old, snapshot)
                if not changes:
                    continue
                print(
                    f"{current_time()} STATE {short_id(snapshot.bubble_id)} "
                    f"phase={snapshot.inferred_phase}"
                )
                for key, (old_value, new_value) in changes.items():
                    print(f"  {key}: {old_value!r} -> {new_value!r}")
            previous = current
    except KeyboardInterrupt:
        print("\nWatch stopped.")


def current_time() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def main() -> int:
    args = parse_args()
    try:
        with connect_read_only(args.database) as connection:
            validate_schema(connection)
            query_only = bool(connection.execute("PRAGMA query_only").fetchone()[0])
            composer_id = args.composer_id or read_selected_id(connection)
            snapshots = read_recent(connection, composer_id, args.recent)
            if args.json:
                print_json(
                    snapshots,
                    composer_id,
                    args.database,
                    query_only,
                )
            else:
                print_human(
                    snapshots,
                    composer_id,
                    args.database,
                    query_only,
                )
            if args.watch:
                watch_state(
                    connection,
                    args,
                    composer_id,
                    snapshots,
                )
            return 0
    except (FileNotFoundError, sqlite3.Error, RuntimeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
