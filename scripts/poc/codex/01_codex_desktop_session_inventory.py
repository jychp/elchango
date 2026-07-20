#!/usr/bin/env python3
"""POC 01: inventory persistent Codex Desktop sessions.

Purpose
=======
Test whether the Codex desktop app (shipped inside ChatGPT: bundle
``com.openai.codex``, URL scheme ``codex://``) exposes enough read-only local
state to:

1. enumerate its complete persistent session list, not only live windows;
2. retain a stable native session identity per thread;
3. associate every session with its working directory and git repository;
4. resolve a human-readable title without reading conversation content;
5. sort sessions by a provider-neutral last-activity time.

Scope: Codex *Desktop* sessions only. Sessions authored by the Codex CLI
(``codex-tui``, ``codex_exec``, ``codex_cli_rs``) and subagent threads
(``thread_source`` other than ``user``) are excluded, matching the elChango
provider scope agreed for issue #8.

Method
======
The Codex desktop app persists one JSON-lines rollout per session run under
``~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl``. The first line is a
``session_meta`` record whose ``payload`` carries the identity and workspace
fields. This POC reads only that first line, never the conversation body.

Observed schema varies across ``cli_version`` values, so parsing is defensive:

- native id = ``payload.session_id`` when present, else ``payload.id``;
- a Desktop session is selected only when ``originator == "Codex Desktop"``
  and ``thread_source == "user"``;
- ``payload.source`` may be a string (e.g. ``"exec"``) or an object (subagent);
- ``payload.git`` is usually but not always present;
- a thread may span several rollout files after resume; the newest rollout per
  native id wins.

Titles are resolved from ``~/.codex/session_index.jsonl`` (``id`` ->
``thread_name``) and are hidden unless ``--show-titles`` is supplied.

Safety
======
All files are opened read-only. No Codex configuration is modified and no
process is signaled. Only the bounded first line of each rollout is read.

Examples
========
    python scripts/poc/codex/01_codex_desktop_session_inventory.py
    python scripts/poc/codex/01_codex_desktop_session_inventory.py --limit 30
    python scripts/poc/codex/01_codex_desktop_session_inventory.py --json

Interpretation
==============
``INVENTORY_SUPPORTED`` means the observed Desktop user sessions have unique
native identities and workspace paths. It does not prove state detection,
selected-session detection, focus targeting, or stability across Codex Desktop
upgrades.

Official references
===================
https://developers.openai.com/codex/
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_CODEX_ROOT = Path.home() / ".codex"
DESKTOP_ORIGINATOR = "Codex Desktop"
DESKTOP_THREAD_SOURCE = "user"
MAX_META_LINE_BYTES = 1024 * 1024


class SchemaError(RuntimeError):
    """An undocumented Codex rollout record no longer matches observations."""


@dataclass(frozen=True)
class Session:
    """Sanitized metadata for one persistent Codex Desktop session."""

    native_id: str
    rollout_id: str
    cwd: str
    repository_url: str | None
    last_activity_at_ms: int
    cli_version: str | None
    rollout_path: str
    rollout_count: int
    title: str | None


@dataclass(frozen=True)
class Evidence:
    """Machine-readable observations and conservative POC verdict."""

    codex_sessions_root: str
    session_index_path: str
    rollout_files_scanned: int
    desktop_user_rollouts: int
    distinct_sessions: int
    unique_native_ids: bool
    workspace_mapped_sessions: int
    repository_mapped_sessions: int
    title_mapped_sessions: int
    inventory_verdict: str
    state_verdict: str
    selected_verdict: str
    targeting_verdict: str
    limitations: tuple[str, ...]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only persistent Codex Desktop session inventory.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "The Codex rollout schema is undocumented and varies by version.\n"
            "The parser reads only the bounded first line of each rollout and\n"
            "fails conservatively when required fields move or change."
        ),
    )
    parser.add_argument(
        "--codex-root",
        type=Path,
        default=DEFAULT_CODEX_ROOT,
        help=f"Codex home directory (default: {DEFAULT_CODEX_ROOT})",
    )
    parser.add_argument(
        "--show-titles",
        action="store_true",
        help="Show session titles, which may contain sensitive task text.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum sessions to list (default: 20; 0 means unlimited).",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.limit < 0:
        parser.error("--limit must be zero or positive")
    return args


def read_session_meta(path: Path) -> dict[str, Any] | None:
    """Decode the bounded first rollout line without reading the body.

    Returns the ``payload`` object for a usable ``session_meta`` record, or
    ``None`` when the first line is empty, not valid JSON, or not a
    session_meta record. Codex writes occasional empty or partial rollout
    artifacts (for example after a crash); those cannot even be classified by
    ``originator``, so they are skipped rather than failing the inventory. A
    session_meta record whose required fields are missing still fails hard
    downstream once the record is confirmed to be a Codex Desktop session.
    """

    try:
        with path.open("rb") as source:
            raw = source.readline(MAX_META_LINE_BYTES)
    except OSError as error:
        raise SchemaError(f"{path}: cannot read rollout: {error}") from error
    if not raw.strip():
        return None
    if not raw.endswith(b"\n") and len(raw) >= MAX_META_LINE_BYTES:
        raise SchemaError(f"{path}: session_meta line exceeds the safe prefix")
    try:
        record = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(record, dict) or record.get("type") != "session_meta":
        return None
    payload = record.get("payload")
    if not isinstance(payload, dict):
        raise SchemaError(f"{path}: session_meta payload must be an object")
    return payload


def is_desktop_user(payload: dict[str, Any]) -> bool:
    """Select only Codex Desktop, user-authored threads."""

    return (
        payload.get("originator") == DESKTOP_ORIGINATOR
        and payload.get("thread_source") == DESKTOP_THREAD_SOURCE
    )


def native_id(payload: dict[str, Any], path: Path) -> str:
    """Resolve the stable native session id, tolerating schema drift."""

    for key in ("session_id", "id"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    raise SchemaError(f"{path}: session_meta lacks a string session_id or id")


def parse_iso_ms(value: Any) -> int | None:
    """Parse an ISO 8601 timestamp to epoch milliseconds, or None."""

    if not isinstance(value, str) or not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return int(moment.timestamp() * 1000)


def load_session_index(path: Path) -> dict[str, str]:
    """Map thread id -> title from the optional session index."""

    if not path.is_file():
        return {}
    titles: dict[str, str] = {}
    try:
        with path.open("r", encoding="utf-8", errors="replace") as source:
            for line in source:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                thread_id = record.get("id")
                name = record.get("thread_name")
                if isinstance(thread_id, str) and isinstance(name, str):
                    titles[thread_id] = name
    except OSError as error:
        raise SchemaError(f"{path}: cannot read session index: {error}") from error
    return titles


def inventory(codex_root: Path) -> tuple[list[Session], Evidence]:
    """Build the complete persistent Desktop inventory and verdict."""

    sessions_root = codex_root / "sessions"
    index_path = codex_root / "session_index.jsonl"
    if not sessions_root.is_dir():
        raise FileNotFoundError(f"Codex sessions root not found: {sessions_root}")

    titles = load_session_index(index_path)
    rollouts = sorted(sessions_root.glob("*/*/*/rollout-*.jsonl"))
    scanned = 0
    desktop_user = 0
    newest: dict[str, tuple[Session, int]] = {}
    counts: dict[str, int] = {}

    for path in rollouts:
        scanned += 1
        payload = read_session_meta(path)
        if payload is None or not is_desktop_user(payload):
            continue
        desktop_user += 1
        identity = native_id(payload, path)
        cwd = payload.get("cwd")
        if not isinstance(cwd, str) or not cwd:
            raise SchemaError(f"{path}: cwd must be a non-empty string")
        repository_url: str | None = None
        git = payload.get("git")
        if isinstance(git, dict):
            candidate = git.get("repository_url")
            if isinstance(candidate, str) and candidate:
                repository_url = candidate
        cli_version = payload.get("cli_version")
        if cli_version is not None and not isinstance(cli_version, str):
            raise SchemaError(f"{path}: cli_version must be a string or absent")

        stat = path.stat()
        activity_ms = parse_iso_ms(payload.get("timestamp"))
        last_activity_ms = int(stat.st_mtime * 1000)
        if activity_ms is not None:
            last_activity_ms = max(last_activity_ms, activity_ms)

        counts[identity] = counts.get(identity, 0) + 1
        session = Session(
            native_id=identity,
            rollout_id=str(payload.get("id") or identity),
            cwd=cwd,
            repository_url=repository_url,
            last_activity_at_ms=last_activity_ms,
            cli_version=cli_version if isinstance(cli_version, str) else None,
            rollout_path=str(path),
            rollout_count=0,
            title=titles.get(identity),
        )
        existing = newest.get(identity)
        if existing is None or last_activity_ms >= existing[0].last_activity_at_ms:
            newest[identity] = (session, last_activity_ms)

    sessions = [
        Session(**{**asdict(record), "rollout_count": counts[identity]})
        for identity, (record, _) in newest.items()
    ]
    sessions.sort(key=lambda item: (-item.last_activity_at_ms, item.native_id))

    native_ids = [item.native_id for item in sessions]
    workspace_mapped = sum(bool(item.cwd) for item in sessions)
    repository_mapped = sum(item.repository_url is not None for item in sessions)
    title_mapped = sum(item.title is not None for item in sessions)
    unique_native = len(native_ids) == len(set(native_ids))

    if not sessions:
        verdict = "NO_SESSIONS"
    elif unique_native and workspace_mapped == len(sessions):
        verdict = "INVENTORY_SUPPORTED"
    else:
        verdict = "INVENTORY_INSUFFICIENT"

    evidence = Evidence(
        codex_sessions_root=str(sessions_root),
        session_index_path=str(index_path),
        rollout_files_scanned=scanned,
        desktop_user_rollouts=desktop_user,
        distinct_sessions=len(sessions),
        unique_native_ids=unique_native,
        workspace_mapped_sessions=workspace_mapped,
        repository_mapped_sessions=repository_mapped,
        title_mapped_sessions=title_mapped,
        inventory_verdict=verdict,
        state_verdict="UNPROVEN_REQUIRES_LIVE_HOOK_OBSERVATION",
        selected_verdict="UNPROVEN_NO_STATIC_SELECTED_SESSION_SIGNAL",
        targeting_verdict="UNPROVEN_NO_EXACT_FOCUS_MECHANISM",
        limitations=(
            "The Codex rollout schema is undocumented and varies by version.",
            "Native id stability across Codex Desktop resume is unproven.",
            "Last activity uses the newest rollout timestamp and file mtime.",
            "Titles come from an undocumented session index and may be absent.",
            "No selected-session, focus, launch, or hook behavior was tested.",
        ),
    )
    return sessions, evidence


def display_session(session: Session, show_titles: bool) -> str:
    title = session.title if show_titles else "<hidden>"
    repo = session.repository_url or "<none>"
    return (
        f"{session.native_id} rollouts={session.rollout_count} "
        f"version={session.cli_version or '<unknown>'}\n"
        f"  cwd={session.cwd}\n"
        f"  repo={repo} title={title}"
    )


def main() -> int:
    args = parse_args()
    try:
        sessions, evidence = inventory(args.codex_root)
    except (FileNotFoundError, SchemaError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2

    visible = sessions[: args.limit] if args.limit else sessions

    if args.json:
        print(
            json.dumps(
                {
                    "sessions": [
                        {
                            **asdict(session),
                            "title": session.title if args.show_titles else None,
                        }
                        for session in visible
                    ],
                    "evidence": asdict(evidence),
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print("Codex Desktop persistent session inventory")
        print(f"Inventory verdict: {evidence.inventory_verdict}")
        print(f"State verdict: {evidence.state_verdict}")
        print(f"Selected-session verdict: {evidence.selected_verdict}")
        print(f"Targeting verdict: {evidence.targeting_verdict}")
        print(
            f"Rollouts scanned: {evidence.rollout_files_scanned}; "
            f"Desktop user rollouts: {evidence.desktop_user_rollouts}; "
            f"distinct sessions: {evidence.distinct_sessions}"
        )
        for session in visible:
            print(display_session(session, args.show_titles))
        print("Limitations:")
        for limitation in evidence.limitations:
            print(f"- {limitation}")
    return 0 if evidence.inventory_verdict in {
        "INVENTORY_SUPPORTED",
        "NO_SESSIONS",
    } else 1


if __name__ == "__main__":
    raise SystemExit(main())
