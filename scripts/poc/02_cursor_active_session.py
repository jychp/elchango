#!/usr/bin/env python3
"""POC M0.2: detect Cursor's selected agent session from local state.

Purpose
=======
Test whether the undocumented ``cursor/glass.selectedAgent`` value in Cursor's
global ``state.vscdb`` can identify one selected native agent session.

The POC validates that the selected ID:

1. resolves to a composer header;
2. represents a non-archived, non-draft, non-subagent session;
3. maps to a workspace path;
4. reports the current visibility marker as an auxiliary signal;
5. changes consistently while the user switches sessions.

Terminology
===========
"Selected agent" is the session Cursor records in
``cursor/glass.selectedAgent``. It may remain selected while a diff, browser,
canvas, terminal, or file tab is in front. This POC does not claim that the
selected agent owns keyboard focus.

Safety
======
The database is opened with SQLite ``mode=ro`` and ``PRAGMA query_only=ON``.
The POC uses only the Python standard library and does not modify Cursor state.

Examples
========
    python scripts/poc/02_cursor_active_session.py
    python scripts/poc/02_cursor_active_session.py --json
    python scripts/poc/02_cursor_active_session.py --watch 60
    python scripts/poc/02_cursor_active_session.py --expect-workspace elchango

Interpretation
==============
``SUPPORTED`` means the selected-agent key resolves to a valid candidate with a
workspace mapping. Visibility is reported but is not required because live
testing showed that it can lag or disagree during a valid session switch.

``AMBIGUOUS`` means the key resolves, but one or more corroborating signals are
missing or contradictory. The safe consumer behavior is to report ``unknown``.

``UNSUPPORTED`` means the key or required schema is absent or invalid.

All inspected state is undocumented and version-fragile. Revalidate this POC
after Cursor upgrades.

Observed on July 16, 2026
=========================
A 90-second live test observed three switches among sessions in different
workspaces. ``cursor/glass.selectedAgent`` matched the user's selected session
each time. Opening a canvas did not change the selected ID. One valid switch had
``visible=false``, proving that visibility cannot be required as corroboration.
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
from typing import Any
from urllib.parse import unquote, urlparse


DEFAULT_CURSOR_ROOT = Path.home() / "Library/Application Support/Cursor/User"
DEFAULT_DATABASE = DEFAULT_CURSOR_ROOT / "globalStorage/state.vscdb"
DEFAULT_WORKSPACE_STORAGE = DEFAULT_CURSOR_ROOT / "workspaceStorage"
SELECTED_AGENT_KEY = "cursor/glass.selectedAgent"
VISIBILITY_PREFIX = "glass/cursor.editorPanelVisibility.agent/"
TABS_PREFIX = "cursor/glass.tabs.v2/"
REQUIRED_HEADER_COLUMNS = {
    "composerId",
    "workspaceId",
    "lastUpdatedAt",
    "isArchived",
    "isSubagent",
    "value",
}


@dataclass(frozen=True)
class SelectedAgent:
    """Normalized evidence for Cursor's currently selected agent."""

    composer_id: str | None
    workspace_id: str | None
    workspace_path: str | None
    name: str | None
    archived: bool | None
    draft: bool | None
    ephemeral: bool | None
    subagent: bool | None
    header_updated_at_ms: int | None
    visible: bool | None
    visibility_updated_at_ms: int | None
    visible_marker_count: int
    newest_visible_marker: bool | None
    foreground_tab_kind: str | None
    foreground_tab_id: str | None
    verdict: str
    reasons: tuple[str, ...]

    def fingerprint(self) -> tuple[Any, ...]:
        return (
            self.composer_id,
            self.workspace_id,
            self.archived,
            self.draft,
            self.ephemeral,
            self.subagent,
            self.visible,
            self.visibility_updated_at_ms,
            self.foreground_tab_kind,
            self.foreground_tab_id,
            self.verdict,
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only detection of Cursor's selected native agent.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "This POC identifies Cursor's selected agent, not necessarily the control with\n"
            "keyboard focus. Use --watch while switching agents and opening non-agent tabs.\n"
            "A consumer must return unknown for AMBIGUOUS or UNSUPPORTED results."
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
        "--expect-id",
        help="Require the selected composer ID to start with this value.",
    )
    parser.add_argument(
        "--expect-workspace",
        help="Require the selected workspace path to contain this text.",
    )
    parser.add_argument(
        "--show-name",
        action="store_true",
        help="Display the session name, which may contain sensitive prompt text.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON output.")
    parser.add_argument(
        "--watch",
        type=float,
        metavar="SECONDS",
        help="Watch selected-agent changes for this duration.",
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


def read_selected_id(connection: sqlite3.Connection) -> str | None:
    row = connection.execute(
        "SELECT value FROM ItemTable WHERE key = ?",
        (SELECTED_AGENT_KEY,),
    ).fetchone()
    if row is None:
        return None
    value = decode_text(row["value"])
    return value.strip() if value and value.strip() else None


def read_visibility(
    connection: sqlite3.Connection,
) -> dict[str, tuple[bool, int | None]]:
    markers: dict[str, tuple[bool, int | None]] = {}
    rows = connection.execute(
        "SELECT key, value FROM ItemTable WHERE key LIKE ?",
        (f"{VISIBILITY_PREFIX}%",),
    )
    for row in rows:
        payload = parse_json(row["value"])
        visible = payload.get("visible")
        if not isinstance(visible, bool):
            continue
        updated_at = payload.get("updatedAtMs")
        markers[str(row["key"]).removeprefix(VISIBILITY_PREFIX)] = (
            visible,
            updated_at if isinstance(updated_at, int) else None,
        )
    return markers


def read_foreground_tab(
    connection: sqlite3.Connection,
    workspace_id: str,
) -> tuple[str | None, str | None]:
    row = connection.execute(
        "SELECT value FROM ItemTable WHERE key = ?",
        (f"{TABS_PREFIX}{workspace_id}/state.json",),
    ).fetchone()
    if row is None:
        return None, None
    active_target = parse_json(row["value"]).get("activeTarget")
    if not isinstance(active_target, dict):
        return None, None
    scope = active_target.get("scope")
    tab_id = active_target.get("tabId")
    return (
        scope if isinstance(scope, str) else None,
        tab_id if isinstance(tab_id, str) else None,
    )


def inspect_selected_agent(
    connection: sqlite3.Connection,
    workspace_paths: dict[str, str],
) -> SelectedAgent:
    composer_id = read_selected_id(connection)
    if composer_id is None:
        return SelectedAgent(
            composer_id=None,
            workspace_id=None,
            workspace_path=None,
            name=None,
            archived=None,
            draft=None,
            ephemeral=None,
            subagent=None,
            header_updated_at_ms=None,
            visible=None,
            visibility_updated_at_ms=None,
            visible_marker_count=0,
            newest_visible_marker=None,
            foreground_tab_kind=None,
            foreground_tab_id=None,
            verdict="UNSUPPORTED",
            reasons=(f"ItemTable key {SELECTED_AGENT_KEY!r} is absent or empty.",),
        )

    row = connection.execute(
        """
        SELECT composerId, workspaceId, lastUpdatedAt, isArchived, isSubagent, value
        FROM composerHeaders
        WHERE composerId = ?
        """,
        (composer_id,),
    ).fetchone()
    if row is None:
        return SelectedAgent(
            composer_id=composer_id,
            workspace_id=None,
            workspace_path=None,
            name=None,
            archived=None,
            draft=None,
            ephemeral=None,
            subagent=None,
            header_updated_at_ms=None,
            visible=None,
            visibility_updated_at_ms=None,
            visible_marker_count=0,
            newest_visible_marker=None,
            foreground_tab_kind=None,
            foreground_tab_id=None,
            verdict="UNSUPPORTED",
            reasons=("The selected ID does not resolve to composerHeaders.",),
        )

    header = parse_json(row["value"])
    workspace_id = str(row["workspaceId"] or "")
    workspace_path = embedded_workspace_path(header) or workspace_paths.get(workspace_id)
    markers = read_visibility(connection)
    visible, visibility_updated_at = markers.get(composer_id, (None, None))
    visible_markers = {
        agent_id: updated_at
        for agent_id, (is_visible, updated_at) in markers.items()
        if is_visible
    }
    newest_visible_timestamp = max(
        (updated_at for updated_at in visible_markers.values() if updated_at is not None),
        default=None,
    )
    newest_visible_marker = (
        visibility_updated_at == newest_visible_timestamp
        if visibility_updated_at is not None and newest_visible_timestamp is not None
        else None
    )
    foreground_kind, foreground_id = read_foreground_tab(connection, workspace_id)

    archived = bool(row["isArchived"])
    draft = bool(header.get("isDraft", False))
    ephemeral = bool(header.get("isEphemeral", False))
    subagent = bool(row["isSubagent"])
    reasons: list[str] = []
    if archived:
        reasons.append("Selected composer is archived.")
    if draft:
        reasons.append("Selected composer is a draft.")
    if ephemeral:
        reasons.append("Selected composer is ephemeral.")
    if subagent:
        reasons.append("Selected composer is a subagent.")
    if workspace_path is None:
        reasons.append("Selected composer has no mapped workspace path.")
    verdict = "SUPPORTED" if not reasons else "AMBIGUOUS"
    if visible is not True:
        reasons.append(
            "Selected composer lacks visible=true; live testing proved this "
            "auxiliary signal can disagree during a valid switch."
        )
    if visibility_updated_at is None:
        reasons.append("Selected composer lacks an auxiliary visibility timestamp.")
    if len(visible_markers) > 1:
        reasons.append(
            f"{len(visible_markers)} visible=true markers coexist; "
            "selectedAgent remains the primary signal."
        )
    if newest_visible_marker is False:
        reasons.append(
            "Selected composer does not have the newest visibility timestamp."
        )

    return SelectedAgent(
        composer_id=composer_id,
        workspace_id=workspace_id,
        workspace_path=workspace_path,
        name=header.get("name") if isinstance(header.get("name"), str) else None,
        archived=archived,
        draft=draft,
        ephemeral=ephemeral,
        subagent=subagent,
        header_updated_at_ms=as_int(row["lastUpdatedAt"]),
        visible=visible,
        visibility_updated_at_ms=visibility_updated_at,
        visible_marker_count=len(visible_markers),
        newest_visible_marker=newest_visible_marker,
        foreground_tab_kind=foreground_kind,
        foreground_tab_id=foreground_id,
        verdict=verdict,
        reasons=tuple(reasons),
    )


def as_int(value: Any) -> int | None:
    return value if isinstance(value, int) else None


def format_time(timestamp_ms: int | None) -> str:
    if timestamp_ms is None:
        return "unknown"
    return datetime.fromtimestamp(timestamp_ms / 1000).astimezone().isoformat(
        timespec="milliseconds"
    )


def display_id(composer_id: str | None) -> str:
    if composer_id is None or len(composer_id) <= 12:
        return composer_id or "<none>"
    return f"{composer_id[:8]}…"


def expectation_failures(
    selected: SelectedAgent,
    expect_id: str | None,
    expect_workspace: str | None,
) -> list[str]:
    failures = []
    if expect_id and not (selected.composer_id or "").startswith(expect_id):
        failures.append(
            f"Expected composer ID prefix {expect_id!r}, got {selected.composer_id!r}."
        )
    if expect_workspace and expect_workspace.casefold() not in (
        selected.workspace_path or ""
    ).casefold():
        failures.append(
            f"Expected workspace containing {expect_workspace!r}, "
            f"got {selected.workspace_path!r}."
        )
    return failures


def print_human(
    selected: SelectedAgent,
    args: argparse.Namespace,
    query_only: bool,
) -> None:
    print("Cursor selected-agent POC M0.2")
    print(f"Database: {args.database}")
    print(f"Safety: mode=ro, query_only={str(query_only).lower()}")
    print()
    print(f"Selected composer: {selected.composer_id or '<none>'}")
    print(f"Workspace: {selected.workspace_path or '<unmapped>'}")
    if args.show_name:
        print(f"Name: {selected.name or '<none>'}")
    print(f"Header updated: {format_time(selected.header_updated_at_ms)}")
    print(
        "Visibility: "
        f"{selected.visible}, updated={format_time(selected.visibility_updated_at_ms)}, "
        f"newest={selected.newest_visible_marker}"
    )
    print(f"Coexisting visible markers: {selected.visible_marker_count}")
    print(
        "Foreground workspace tab: "
        f"{selected.foreground_tab_kind or '<unknown>'}/"
        f"{selected.foreground_tab_id or '<unknown>'}"
    )
    print()
    print(f"VERDICT: {selected.verdict}")
    for reason in selected.reasons:
        print(f"  - {reason}")

    failures = expectation_failures(
        selected,
        args.expect_id,
        args.expect_workspace,
    )
    if failures:
        print("EXPECTATION: FAILED")
        for failure in failures:
            print(f"  - {failure}")
    elif args.expect_id or args.expect_workspace:
        print("EXPECTATION: PASSED")


def print_json(
    selected: SelectedAgent,
    args: argparse.Namespace,
    query_only: bool,
) -> None:
    payload = asdict(selected)
    if not args.show_name:
        payload["name"] = None
    payload["database"] = str(args.database)
    payload["query_only"] = query_only
    payload["expectation_failures"] = expectation_failures(
        selected,
        args.expect_id,
        args.expect_workspace,
    )
    print(json.dumps(payload, indent=2, sort_keys=True))


def watch_selected_agent(
    connection: sqlite3.Connection,
    workspace_paths: dict[str, str],
    args: argparse.Namespace,
    initial: SelectedAgent,
) -> SelectedAgent:
    previous = initial
    latest = initial
    deadline = time.monotonic() + args.watch
    print()
    print(f"Watching for {args.watch:g}s every {args.interval:g}s. Press Ctrl-C to stop.")
    try:
        while time.monotonic() < deadline:
            time.sleep(min(args.interval, max(0, deadline - time.monotonic())))
            current = inspect_selected_agent(connection, workspace_paths)
            latest = current
            if current.fingerprint() == previous.fingerprint():
                continue
            now = datetime.now().astimezone().isoformat(timespec="milliseconds")
            if current.composer_id != previous.composer_id:
                print(
                    f"{now} SELECTED {display_id(previous.composer_id)} -> "
                    f"{display_id(current.composer_id)} "
                    f"workspace={current.workspace_path or '<unmapped>'} "
                    f"verdict={current.verdict}"
                )
            else:
                print(
                    f"{now} REFRESHED {display_id(current.composer_id)} "
                    f"visible={current.visible} "
                    f"visibility_updated={format_time(current.visibility_updated_at_ms)} "
                    f"foreground={current.foreground_tab_kind or '<unknown>'}/"
                    f"{current.foreground_tab_id or '<unknown>'} "
                    f"verdict={current.verdict}"
                )
            previous = current
    except KeyboardInterrupt:
        print("\nWatch stopped.")
    return latest


def main() -> int:
    args = parse_args()
    try:
        with connect_read_only(args.database) as connection:
            validate_schema(connection)
            query_only = bool(connection.execute("PRAGMA query_only").fetchone()[0])
            workspace_paths = load_workspace_paths(args.workspace_storage)
            selected = inspect_selected_agent(connection, workspace_paths)
            if args.json:
                print_json(selected, args, query_only)
            else:
                print_human(selected, args, query_only)
            if args.watch:
                selected = watch_selected_agent(
                    connection,
                    workspace_paths,
                    args,
                    selected,
                )
            failures = expectation_failures(
                selected,
                args.expect_id,
                args.expect_workspace,
            )
            if failures:
                return 1
            return 0 if selected.verdict == "SUPPORTED" else 1
    except (FileNotFoundError, sqlite3.Error, RuntimeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
