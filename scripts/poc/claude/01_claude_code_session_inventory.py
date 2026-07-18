#!/usr/bin/env python3
"""POC V3.2: inventory persistent Claude Code sessions from Claude Desktop.

Purpose
=======
Test whether Claude Desktop exposes enough read-only local state to:

1. enumerate its complete persistent Claude Code session list;
2. retain sessions that do not currently have a live Claude process;
3. associate every session with its working directory;
4. correlate Desktop identity, Claude Code hook identity, and transcript;
5. sort sessions by provider-neutral last activity time.

This experiment covers Claude Code sessions in Claude Desktop on macOS. It does
not cover Cowork or ordinary Claude chats.

Method
======
Claude Desktop stores one persistent JSON record per Code session under
``~/Library/Application Support/Claude/claude-code-sessions``. Each record has
a Desktop ``sessionId`` such as ``local_<uuid>`` and a ``cliSessionId`` matching
Claude Code's process registry, transcript filename, and official hook
``session_id``.

The Desktop files can contain large conversation payloads. This POC uses a
bounded streaming-prefix parser and stops as soon as the required top-level
metadata has been decoded. It never reads messages, prompts, tool inputs, or
tool outputs. ``~/.claude/sessions`` is used only to annotate which persistent
sessions currently have a live process. It is not the inventory source.

Safety
======
All files are opened read-only. The POC never modifies Claude configuration or
signals a process. ``os.kill(pid, 0)`` checks process existence without
delivering a signal. Titles are hidden unless ``--show-titles`` is supplied.

Examples
========
    python scripts/poc/claude/01_claude_code_session_inventory.py
    python scripts/poc/claude/01_claude_code_session_inventory.py --limit 30
    python scripts/poc/claude/01_claude_code_session_inventory.py --include-archived
    python scripts/poc/claude/01_claude_code_session_inventory.py --json

Interpretation
==============
``INVENTORY_SUPPORTED`` means non-archived persistent records have unique
Desktop and CLI identities, workspace paths, and unambiguous transcript
mappings. It does not prove state detection, targeting, or stability across
Claude Desktop upgrades.

Official references
===================
https://docs.anthropic.com/en/docs/claude-code/hooks
https://support.claude.com/en/articles/14729294-open-claude-desktop-with-a-link
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


DEFAULT_DESKTOP_ROOT = (
    Path.home() / "Library/Application Support/Claude/claude-code-sessions"
)
DEFAULT_CLAUDE_ROOT = Path.home() / ".claude"
DEFAULT_PROCESS_DIR = DEFAULT_CLAUDE_ROOT / "sessions"
DEFAULT_PROJECTS_DIR = DEFAULT_CLAUDE_ROOT / "projects"
MAX_METADATA_PREFIX_BYTES = 64 * 1024
REQUIRED_DESKTOP_FIELDS = {
    "sessionId",
    "cliSessionId",
    "cwd",
    "originCwd",
    "createdAt",
    "lastActivityAt",
    "isArchived",
}
OPTIONAL_DESKTOP_FIELDS = {"title"}


class SchemaError(RuntimeError):
    """An undocumented Claude local record no longer matches observations."""


@dataclass(frozen=True)
class Session:
    """Sanitized metadata for one persistent Claude Desktop Code session."""

    desktop_session_id: str
    cli_session_id: str
    cwd: str
    origin_cwd: str
    created_at_ms: int
    last_activity_at_ms: int
    archived: bool
    title: str | None
    process_alive: bool
    transcript_path: str | None


@dataclass(frozen=True)
class Evidence:
    """Machine-readable observations and conservative POC verdict."""

    desktop_sessions_root: str
    process_directory: str
    projects_directory: str
    persistent_records: int
    non_archived_sessions: int
    live_process_sessions: int
    unique_desktop_session_ids: bool
    unique_cli_session_ids: bool
    workspace_mapped_sessions: int
    transcript_mapped_sessions: int
    ambiguous_transcript_sessions: int
    inventory_verdict: str
    state_verdict: str
    targeting_verdict: str
    limitations: tuple[str, ...]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only persistent Claude Desktop Code session inventory.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "The Desktop record schema is undocumented. The parser reads only a\n"
            "bounded metadata prefix and fails if required fields move or change."
        ),
    )
    parser.add_argument(
        "--desktop-sessions-root",
        type=Path,
        default=DEFAULT_DESKTOP_ROOT,
        help=f"Persistent Desktop Code records (default: {DEFAULT_DESKTOP_ROOT})",
    )
    parser.add_argument(
        "--process-dir",
        type=Path,
        default=DEFAULT_PROCESS_DIR,
        help=f"Optional live process records (default: {DEFAULT_PROCESS_DIR})",
    )
    parser.add_argument(
        "--projects-dir",
        type=Path,
        default=DEFAULT_PROJECTS_DIR,
        help=f"Claude transcript root (default: {DEFAULT_PROJECTS_DIR})",
    )
    parser.add_argument(
        "--include-archived",
        action="store_true",
        help="Include Desktop sessions marked archived.",
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


def process_exists(pid: int) -> bool:
    """Check process existence without delivering a signal."""

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def read_top_level_metadata(path: Path) -> dict[str, Any]:
    """Decode required leading object fields without reading message content."""

    try:
        with path.open("r", encoding="utf-8") as source:
            prefix = source.read(MAX_METADATA_PREFIX_BYTES)
    except (OSError, UnicodeDecodeError) as error:
        raise SchemaError(f"{path}: cannot read metadata prefix: {error}") from error
    if not prefix.startswith("{"):
        raise SchemaError(f"{path}: Desktop record must be a JSON object")

    decoder = json.JSONDecoder()
    values: dict[str, Any] = {}
    position = 1
    try:
        while True:
            position = _skip_whitespace(prefix, position)
            key, position = decoder.raw_decode(prefix, position)
            if not isinstance(key, str):
                raise SchemaError(f"{path}: top-level field name must be a string")
            if not (REQUIRED_DESKTOP_FIELDS - values.keys()) and (
                key not in OPTIONAL_DESKTOP_FIELDS
            ):
                break
            position = _skip_whitespace(prefix, position)
            if position >= len(prefix) or prefix[position] != ":":
                raise SchemaError(f"{path}: missing colon after {key}")
            position = _skip_whitespace(prefix, position + 1)
            value, position = decoder.raw_decode(prefix, position)
            if key in REQUIRED_DESKTOP_FIELDS | OPTIONAL_DESKTOP_FIELDS:
                values[key] = value
            if not (REQUIRED_DESKTOP_FIELDS - values.keys()) and (
                OPTIONAL_DESKTOP_FIELDS <= values.keys()
            ):
                break
            position = _skip_whitespace(prefix, position)
            if position >= len(prefix) or prefix[position] not in {",", "}"}:
                raise SchemaError(f"{path}: invalid separator after {key}")
            if prefix[position] == "}":
                break
            position += 1
    except json.JSONDecodeError as error:
        raise SchemaError(
            f"{path}: required metadata exceeds the "
            f"{MAX_METADATA_PREFIX_BYTES}-byte safe prefix or is invalid JSON"
        ) from error

    missing = REQUIRED_DESKTOP_FIELDS - values.keys()
    if missing:
        raise SchemaError(f"{path}: missing required fields: {sorted(missing)}")
    return values


def _skip_whitespace(text: str, position: int) -> int:
    while position < len(text) and text[position].isspace():
        position += 1
    return position


def live_cli_session_ids(process_dir: Path) -> set[str]:
    """Read the optional process registry only as a live-state annotation."""

    if not process_dir.is_dir():
        return set()
    session_ids: set[str] = set()
    for path in process_dir.glob("*.json"):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise SchemaError(f"{path}: invalid process record: {error}") from error
        if not isinstance(value, dict):
            raise SchemaError(f"{path}: process record must be an object")
        pid = _required_integer(value, "pid", path)
        if pid <= 0:
            raise SchemaError(f"{path}: pid must be a positive integer")
        session_id = _required_string(value, "sessionId", path)
        if process_exists(pid):
            session_ids.add(session_id)
    return session_ids


def transcript_candidates(projects_dir: Path, cli_session_id: str) -> tuple[Path, ...]:
    """Find top-level transcripts while excluding subagent transcripts."""

    return tuple(
        sorted(
            candidate
            for candidate in projects_dir.glob(f"*/{cli_session_id}.jsonl")
            if candidate.is_file()
        )
    )


def load_desktop_session(
    path: Path,
    projects_dir: Path,
    live_cli_ids: set[str],
) -> tuple[Session, int]:
    """Parse one bounded Desktop metadata prefix."""

    value = read_top_level_metadata(path)
    desktop_id = _required_string(value, "sessionId", path)
    cli_id = _required_string(value, "cliSessionId", path)
    cwd = _required_string(value, "cwd", path)
    origin_cwd = _required_string(value, "originCwd", path)
    created_at = _required_integer(value, "createdAt", path)
    last_activity = _required_integer(value, "lastActivityAt", path)
    archived = value.get("isArchived")
    if not isinstance(archived, bool):
        raise SchemaError(f"{path}: isArchived must be a boolean")
    title = value.get("title")
    if title is not None and not isinstance(title, str):
        raise SchemaError(f"{path}: title must be a string or null")
    if path.stem != desktop_id:
        raise SchemaError(f"{path}: filename must match Desktop sessionId")

    candidates = transcript_candidates(projects_dir, cli_id)
    return (
        Session(
            desktop_session_id=desktop_id,
            cli_session_id=cli_id,
            cwd=cwd,
            origin_cwd=origin_cwd,
            created_at_ms=created_at,
            last_activity_at_ms=last_activity,
            archived=archived,
            title=title,
            process_alive=cli_id in live_cli_ids,
            transcript_path=str(candidates[0]) if len(candidates) == 1 else None,
        ),
        len(candidates),
    )


def inventory(
    desktop_root: Path,
    process_dir: Path,
    projects_dir: Path,
) -> tuple[list[Session], Evidence]:
    """Build the complete persistent inventory and conservative verdict."""

    if not desktop_root.is_dir():
        raise FileNotFoundError(f"Desktop Code session root not found: {desktop_root}")
    if not projects_dir.is_dir():
        raise FileNotFoundError(f"Claude projects directory not found: {projects_dir}")

    records = sorted(desktop_root.glob("*/*/local_*.json"))
    live_cli_ids = live_cli_session_ids(process_dir)
    sessions: list[Session] = []
    ambiguous = 0
    for path in records:
        session, candidate_count = load_desktop_session(
            path,
            projects_dir,
            live_cli_ids,
        )
        sessions.append(session)
        if not session.archived and candidate_count > 1:
            ambiguous += 1

    sessions.sort(
        key=lambda session: (-session.last_activity_at_ms, session.desktop_session_id)
    )
    visible = [session for session in sessions if not session.archived]
    desktop_ids = [session.desktop_session_id for session in visible]
    cli_ids = [session.cli_session_id for session in visible]
    workspace_mapped = sum(bool(session.cwd) for session in visible)
    transcript_mapped = sum(session.transcript_path is not None for session in visible)
    unique_desktop = len(desktop_ids) == len(set(desktop_ids))
    unique_cli = len(cli_ids) == len(set(cli_ids))

    if not visible:
        verdict = "NO_SESSIONS"
    elif (
        unique_desktop
        and unique_cli
        and workspace_mapped == len(visible)
        and transcript_mapped == len(visible)
        and ambiguous == 0
    ):
        verdict = "INVENTORY_SUPPORTED"
    else:
        verdict = "INVENTORY_INSUFFICIENT"

    return sessions, Evidence(
        desktop_sessions_root=str(desktop_root),
        process_directory=str(process_dir),
        projects_directory=str(projects_dir),
        persistent_records=len(records),
        non_archived_sessions=len(visible),
        live_process_sessions=sum(session.process_alive for session in visible),
        unique_desktop_session_ids=unique_desktop,
        unique_cli_session_ids=unique_cli,
        workspace_mapped_sessions=workspace_mapped,
        transcript_mapped_sessions=transcript_mapped,
        ambiguous_transcript_sessions=ambiguous,
        inventory_verdict=verdict,
        state_verdict="UNPROVEN_REQUIRES_HOOK_OBSERVATION",
        targeting_verdict="UNPROVEN_NO_EXACT_FOCUS_MECHANISM",
        limitations=(
            "Claude Desktop's claude-code-sessions schema is undocumented.",
            "Identity stability across Desktop upgrades is unproven.",
            "Process presence is only an annotation, never an inventory filter.",
            "Archived semantics are observed but undocumented.",
            "No live hooks, focus actions, or launches were tested.",
        ),
    )


def _required_string(value: dict[str, Any], key: str, path: Path) -> str:
    candidate = value.get(key)
    if not isinstance(candidate, str) or not candidate:
        raise SchemaError(f"{path}: {key} must be a non-empty string")
    return candidate


def _required_integer(value: dict[str, Any], key: str, path: Path) -> int:
    candidate = value.get(key)
    if isinstance(candidate, bool) or not isinstance(candidate, int) or candidate < 0:
        raise SchemaError(f"{path}: {key} must be a non-negative integer")
    return candidate


def display_session(session: Session, show_titles: bool) -> str:
    title = session.title if show_titles else "<hidden>"
    transcript = session.transcript_path or "<unmapped>"
    return (
        f"{session.desktop_session_id} cli={session.cli_session_id} "
        f"live={session.process_alive} archived={session.archived}\n"
        f"  cwd={session.cwd}\n"
        f"  title={title} transcript={transcript}"
    )


def main() -> int:
    args = parse_args()
    try:
        sessions, evidence = inventory(
            args.desktop_sessions_root,
            args.process_dir,
            args.projects_dir,
        )
    except (FileNotFoundError, SchemaError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2

    visible = sessions if args.include_archived else [
        session for session in sessions if not session.archived
    ]
    if args.limit:
        visible = visible[: args.limit]

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
        print("Claude Desktop persistent Code session inventory")
        print(f"Inventory verdict: {evidence.inventory_verdict}")
        print(f"State verdict: {evidence.state_verdict}")
        print(f"Targeting verdict: {evidence.targeting_verdict}")
        print(
            f"Persistent: {evidence.persistent_records}; "
            f"non-archived: {evidence.non_archived_sessions}; "
            f"live processes: {evidence.live_process_sessions}"
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
