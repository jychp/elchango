#!/usr/bin/env python3
"""POC M0.1: inventory Cursor agent sessions from local state.

Purpose
=======
Test whether the current macOS Cursor installation exposes enough read-only
local state to:

1. enumerate agent sessions with stable-looking identifiers;
2. associate sessions with workspaces;
3. distinguish archived, draft, and subagent records;
4. identify Cursor's current "visible agent" candidate;
5. observe inventory changes without modifying Cursor data.

This is a proof of concept, not a production API. Cursor's SQLite schema and
keys are undocumented and may change without notice. A successful snapshot
does not prove identifier stability across restarts or Cursor upgrades.

Safety
======
The database is opened with SQLite ``mode=ro`` and ``PRAGMA query_only=ON``.
The POC uses only the Python standard library and never copies or modifies the
database. It reads the live WAL through SQLite, so recent Cursor updates are
visible without stopping the application.

Examples
========
    python scripts/poc/01_cursor_session_inventory.py
    python scripts/poc/01_cursor_session_inventory.py --workspace elchango
    python scripts/poc/01_cursor_session_inventory.py --json
    python scripts/poc/01_cursor_session_inventory.py --watch 30

Use ``--watch`` while opening, switching, archiving, or creating a session.
The resulting change stream is the evidence needed to test update latency and
identifier stability over multiple observations.

Observed on July 16, 2026
=========================
A 90-second run detected a session switch, one new session, visibility changes,
and that session's removal after archival or closure. The new session kept the
same composer ID throughout the observed lifecycle, and changes appeared within
the 0.2-second polling resolution. This validates one live scenario, not
stability across Cursor restarts or upgrades.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import unquote, urlparse


DEFAULT_CURSOR_ROOT = Path.home() / "Library/Application Support/Cursor/User"
DEFAULT_DATABASE = DEFAULT_CURSOR_ROOT / "globalStorage/state.vscdb"
DEFAULT_WORKSPACE_STORAGE = DEFAULT_CURSOR_ROOT / "workspaceStorage"
VISIBILITY_PREFIX = "glass/cursor.editorPanelVisibility.agent/"
REQUIRED_HEADER_COLUMNS = {
    "composerId",
    "workspaceId",
    "createdAt",
    "lastUpdatedAt",
    "isArchived",
    "isSubagent",
    "value",
}


@dataclass(frozen=True)
class Session:
    """Normalized subset of one undocumented Cursor composer header."""

    composer_id: str
    workspace_id: str
    workspace_path: str | None
    created_at_ms: int | None
    updated_at_ms: int | None
    archived: bool
    draft: bool
    ephemeral: bool
    subagent: bool
    visible: bool | None
    visibility_updated_at_ms: int | None
    name: str | None

    def fingerprint(self) -> tuple[Any, ...]:
        """Return fields expected to change during inventory observations."""

        return (
            self.workspace_id,
            self.updated_at_ms,
            self.archived,
            self.draft,
            self.ephemeral,
            self.subagent,
            self.visible,
            self.visibility_updated_at_ms,
        )


@dataclass(frozen=True)
class Evidence:
    """Machine-readable observations and conservative POC verdict."""

    database: str
    journal_mode: str
    query_only: bool
    total_headers: int
    top_level_headers: int
    candidate_sessions: int
    unique_composer_ids: bool
    valid_json_headers: int
    workspace_mapped_candidates: int
    visible_candidates: int
    verdict: str
    limitations: tuple[str, ...]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only inventory of local Cursor agent sessions.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Verdicts:\n"
            "  EXPLOITABLE_WITH_PRECAUTIONS  Snapshot inventory and workspace mapping work,\n"
            "                                but stability or lifecycle semantics remain unproven.\n"
            "  INSUFFICIENT                  Required state is absent or internally inconsistent.\n"
            "\n"
            "The default listing excludes archived, draft, ephemeral, never-updated, and\n"
            "subagent records. These filters describe candidate user sessions, not a proven\n"
            "definition of an open Cursor tab."
        ),
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
        help=f"Cursor workspaceStorage directory (default: {DEFAULT_WORKSPACE_STORAGE})",
    )
    parser.add_argument(
        "--workspace",
        help="Only list sessions whose workspace path contains this case-insensitive text.",
    )
    parser.add_argument("--include-archived", action="store_true")
    parser.add_argument("--include-drafts", action="store_true")
    parser.add_argument(
        "--include-unobserved",
        action="store_true",
        help="Include ephemeral records and records with no observed update.",
    )
    parser.add_argument("--include-subagents", action="store_true")
    parser.add_argument(
        "--show-names",
        action="store_true",
        help="Display Cursor session names, which may contain sensitive prompt text.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum listed sessions in snapshot mode (default: 20; 0 means unlimited).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the snapshot and evidence as JSON.",
    )
    parser.add_argument(
        "--watch",
        type=float,
        metavar="SECONDS",
        help="Watch for changes for this duration after the initial snapshot.",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=0.5,
        help="Polling interval used with --watch (default: 0.5 seconds).",
    )
    args = parser.parse_args()
    if args.limit < 0:
        parser.error("--limit must be zero or positive")
    if args.watch is not None and args.watch <= 0:
        parser.error("--watch must be positive")
    if args.interval <= 0:
        parser.error("--interval must be positive")
    if args.json and args.watch:
        parser.error("--json and --watch cannot be combined")
    return args


def connect_read_only(database: Path) -> sqlite3.Connection:
    """Open the live Cursor database without any write capability."""

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
    """Fail clearly when a Cursor update invalidates the known schema."""

    tables = {
        row["name"]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    missing_tables = {"composerHeaders", "ItemTable"} - tables
    if missing_tables:
        raise RuntimeError(
            f"Unsupported Cursor schema; missing tables: {sorted(missing_tables)}"
        )

    columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(composerHeaders)")
    }
    missing_columns = REQUIRED_HEADER_COLUMNS - columns
    if missing_columns:
        raise RuntimeError(
            f"Unsupported composerHeaders schema; missing columns: {sorted(missing_columns)}"
        )


def file_uri_to_path(uri: str) -> str | None:
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        return None
    return unquote(parsed.path)


def load_workspace_paths(workspace_storage: Path) -> dict[str, str]:
    """Map Cursor workspace hashes to paths from workspace.json files."""

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
    """Prefer the path embedded in the composer header's agent location."""

    candidates = [
        header.get("agentLocation", {}).get("environment", {}).get("uri", {}),
        header.get("workspaceIdentifier", {}).get("uri", {}),
    ]
    for uri in candidates:
        if not isinstance(uri, dict):
            continue
        fs_path = uri.get("fsPath") or uri.get("path")
        if isinstance(fs_path, str) and fs_path:
            return fs_path
        external = uri.get("external")
        if isinstance(external, str):
            path = file_uri_to_path(external)
            if path:
                return path
    return None


def load_visibility(connection: sqlite3.Connection) -> dict[str, tuple[bool, int | None]]:
    """Read Cursor's per-agent visibility markers from ItemTable."""

    visibility: dict[str, tuple[bool, int | None]] = {}
    rows = connection.execute(
        "SELECT key, value FROM ItemTable WHERE key LIKE ?",
        (f"{VISIBILITY_PREFIX}%",),
    )
    for row in rows:
        key = str(row["key"])
        composer_id = key.removeprefix(VISIBILITY_PREFIX)
        try:
            raw = row["value"]
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            payload = json.loads(raw)
            visible = payload.get("visible")
            updated_at = payload.get("updatedAtMs")
            if isinstance(visible, bool):
                visibility[composer_id] = (
                    visible,
                    updated_at if isinstance(updated_at, int) else None,
                )
        except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
            continue
    return visibility


def load_sessions(
    connection: sqlite3.Connection,
    workspace_paths: dict[str, str],
) -> tuple[list[Session], int]:
    """Normalize composerHeaders and return sessions plus valid JSON count."""

    visibility = load_visibility(connection)
    sessions: list[Session] = []
    valid_json_headers = 0
    rows = connection.execute(
        """
        SELECT composerId, workspaceId, createdAt, lastUpdatedAt,
               isArchived, isSubagent, value
        FROM composerHeaders
        ORDER BY lastUpdatedAt DESC
        """
    )
    for row in rows:
        raw = row["value"]
        try:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            header = json.loads(raw)
            if not isinstance(header, dict):
                header = {}
            else:
                valid_json_headers += 1
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
            header = {}

        composer_id = str(row["composerId"])
        workspace_id = str(row["workspaceId"] or "")
        visible, visibility_updated_at = visibility.get(composer_id, (None, None))
        sessions.append(
            Session(
                composer_id=composer_id,
                workspace_id=workspace_id,
                workspace_path=(
                    embedded_workspace_path(header)
                    or workspace_paths.get(workspace_id)
                ),
                created_at_ms=as_int(row["createdAt"]),
                updated_at_ms=as_int(row["lastUpdatedAt"]),
                archived=bool(row["isArchived"]),
                draft=bool(header.get("isDraft", False)),
                ephemeral=bool(header.get("isEphemeral", False)),
                subagent=bool(row["isSubagent"]),
                visible=visible,
                visibility_updated_at_ms=visibility_updated_at,
                name=header.get("name") if isinstance(header.get("name"), str) else None,
            )
        )
    return sessions, valid_json_headers


def as_int(value: Any) -> int | None:
    return value if isinstance(value, int) else None


def candidate_sessions(sessions: Iterable[Session]) -> list[Session]:
    """Apply the conservative default definition used by this POC."""

    return [
        session
        for session in sessions
        if (
            not session.archived
            and not session.draft
            and not session.ephemeral
            and not session.subagent
            and session.updated_at_ms is not None
        )
    ]


def filter_sessions(sessions: Iterable[Session], args: argparse.Namespace) -> list[Session]:
    filtered = []
    workspace_filter = args.workspace.casefold() if args.workspace else None
    for session in sessions:
        if session.archived and not args.include_archived:
            continue
        if session.draft and not args.include_drafts:
            continue
        if (
            (session.ephemeral or session.updated_at_ms is None)
            and not args.include_unobserved
        ):
            continue
        if session.subagent and not args.include_subagents:
            continue
        if workspace_filter and workspace_filter not in (session.workspace_path or "").casefold():
            continue
        filtered.append(session)
    return filtered


def build_evidence(
    connection: sqlite3.Connection,
    database: Path,
    sessions: list[Session],
    valid_json_headers: int,
) -> Evidence:
    candidates = candidate_sessions(sessions)
    composer_ids = [session.composer_id for session in sessions]
    unique_ids = len(composer_ids) == len(set(composer_ids))
    mapped = sum(session.workspace_path is not None for session in candidates)
    visible = sum(session.visible is True for session in candidates)
    inventory_works = bool(sessions) and unique_ids and valid_json_headers == len(sessions)
    mapping_works = bool(candidates) and mapped == len(candidates)
    verdict = (
        "EXPLOITABLE_WITH_PRECAUTIONS"
        if inventory_works and mapping_works
        else "INSUFFICIENT"
    )
    return Evidence(
        database=str(database),
        journal_mode=str(connection.execute("PRAGMA journal_mode").fetchone()[0]),
        query_only=bool(connection.execute("PRAGMA query_only").fetchone()[0]),
        total_headers=len(sessions),
        top_level_headers=sum(not session.subagent for session in sessions),
        candidate_sessions=len(candidates),
        unique_composer_ids=unique_ids,
        valid_json_headers=valid_json_headers,
        workspace_mapped_candidates=mapped,
        visible_candidates=visible,
        verdict=verdict,
        limitations=(
            "isArchived is not proven to mean that an agent tab is currently open or closed.",
            "Several visible=true markers coexist, so visibility is not a global active-session API.",
            "Candidate sessions exclude ephemeral records and records without lastUpdatedAt.",
            "Identifier stability requires repeated observations across restarts and upgrades.",
            "All table names, JSON fields, and ItemTable keys are undocumented Cursor internals.",
        ),
    )


def format_time(timestamp_ms: int | None) -> str:
    if timestamp_ms is None:
        return "unknown"
    return datetime.fromtimestamp(timestamp_ms / 1000).astimezone().isoformat(timespec="seconds")


def display_id(composer_id: str) -> str:
    return composer_id if len(composer_id) <= 12 else f"{composer_id[:8]}…"


def print_human(
    evidence: Evidence,
    sessions: list[Session],
    args: argparse.Namespace,
) -> None:
    print("Cursor session inventory POC M0.1")
    print(f"Database: {evidence.database}")
    print(
        f"Safety: mode=ro, query_only={str(evidence.query_only).lower()}, "
        f"journal={evidence.journal_mode}"
    )
    print()
    print("Observed evidence")
    print(f"  Headers: {evidence.total_headers}")
    print(f"  Top-level headers: {evidence.top_level_headers}")
    print(f"  Candidate sessions: {evidence.candidate_sessions}")
    print(f"  Unique composer IDs: {evidence.unique_composer_ids}")
    print(
        f"  Valid header JSON: {evidence.valid_json_headers}/{evidence.total_headers}"
    )
    print(
        "  Candidate workspace mapping: "
        f"{evidence.workspace_mapped_candidates}/{evidence.candidate_sessions}"
    )
    print(f"  Visible candidates: {evidence.visible_candidates}")
    print()
    print("Sessions")
    shown = sessions if args.limit == 0 else sessions[: args.limit]
    for session in shown:
        state = "visible" if session.visible else "not-visible"
        flags = [
            flag
            for flag, enabled in (
                ("archived", session.archived),
                ("draft", session.draft),
                ("ephemeral", session.ephemeral),
                ("unobserved", session.updated_at_ms is None),
                ("subagent", session.subagent),
            )
            if enabled
        ]
        suffix = f" [{', '.join(flags)}]" if flags else ""
        name = f" name={session.name!r}" if args.show_names and session.name else ""
        print(
            f"  {display_id(session.composer_id)} {state}{suffix} "
            f"updated={format_time(session.updated_at_ms)}"
        )
        print(f"    workspace={session.workspace_path or '<unmapped>'}{name}")
    if len(shown) < len(sessions):
        print(f"  … {len(sessions) - len(shown)} more (use --limit 0)")
    print()
    print(f"VERDICT: {evidence.verdict}")
    print("Limitations")
    for limitation in evidence.limitations:
        print(f"  - {limitation}")


def print_json(evidence: Evidence, sessions: list[Session], args: argparse.Namespace) -> None:
    selected = sessions if args.limit == 0 else sessions[: args.limit]
    payload = {
        "evidence": asdict(evidence),
        "sessions": [
            {
                **asdict(session),
                "name": session.name if args.show_names else None,
            }
            for session in selected
        ],
    }
    print(json.dumps(payload, indent=2, sort_keys=True))


def watch(
    connection: sqlite3.Connection,
    workspace_paths: dict[str, str],
    args: argparse.Namespace,
    initial_sessions: list[Session],
) -> None:
    """Print observable changes for a bounded duration."""

    previous = {session.composer_id: session for session in initial_sessions}
    deadline = time.monotonic() + args.watch
    print()
    print(f"Watching for {args.watch:g}s every {args.interval:g}s. Press Ctrl-C to stop.")
    try:
        while time.monotonic() < deadline:
            time.sleep(min(args.interval, max(0, deadline - time.monotonic())))
            current_all, _ = load_sessions(connection, workspace_paths)
            current = {
                session.composer_id: session
                for session in filter_sessions(current_all, args)
            }
            now = datetime.now().astimezone().isoformat(timespec="milliseconds")
            for composer_id in sorted(current.keys() - previous.keys()):
                print(f"{now} ADDED {display_id(composer_id)}")
            for composer_id in sorted(previous.keys() - current.keys()):
                print(f"{now} REMOVED {display_id(composer_id)}")
            for composer_id in sorted(current.keys() & previous.keys()):
                if current[composer_id].fingerprint() != previous[composer_id].fingerprint():
                    print(
                        f"{now} CHANGED {display_id(composer_id)} "
                        f"visible={current[composer_id].visible} "
                        f"updated={format_time(current[composer_id].updated_at_ms)}"
                    )
            previous = current
    except KeyboardInterrupt:
        print("\nWatch stopped.")


def main() -> int:
    args = parse_args()
    try:
        with connect_read_only(args.database) as connection:
            validate_schema(connection)
            workspace_paths = load_workspace_paths(args.workspace_storage)
            all_sessions, valid_json_headers = load_sessions(
                connection, workspace_paths
            )
            evidence = build_evidence(
                connection,
                args.database,
                all_sessions,
                valid_json_headers,
            )
            selected = filter_sessions(all_sessions, args)
            if args.json:
                print_json(evidence, selected, args)
            else:
                print_human(evidence, selected, args)
            if args.watch:
                watch(connection, workspace_paths, args, selected)
            return 0 if evidence.verdict != "INSUFFICIENT" else 1
    except (FileNotFoundError, sqlite3.Error, RuntimeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
