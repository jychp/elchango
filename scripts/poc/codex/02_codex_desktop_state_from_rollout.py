#!/usr/bin/env python3
"""POC 02: derive Codex Desktop session state from rollout tails.

Purpose
=======
Test whether a Codex Desktop session's current lifecycle state can be inferred
from its persisted rollout file, without any live hook, so the provider has a
conservative fallback when no fresh hook signal exists (mirrors how the Cursor
provider derives state from its database).

Method
======
Each ``~/.codex/sessions/.../rollout-*.jsonl`` file appends one JSON record per
line. Turn lifecycle is marked by ``event_msg`` records with payload types:

- ``task_started``  -> a turn began (working);
- ``task_complete`` -> the turn finished (done);
- ``turn_aborted``  -> the turn was aborted (treated as idle, conservatively).

Rollouts can be multi-megabyte, so this POC reads only a bounded tail window
(``--tail-bytes``) and scans the complete lines within it for the newest
lifecycle marker. The mapping to provider-neutral states is:

- newest marker ``task_started`` (no later terminal marker) -> ``working``;
- newest marker ``task_complete``                            -> ``done``;
- newest marker ``turn_aborted``                             -> ``idle``;
- no marker in the tail window                               -> ``idle``.

Observation, not conclusion: no persisted "waiting for approval" record was
found in Desktop rollouts. The ``waiting`` (orange) state is therefore NOT
derivable from rollouts and must come from live hooks. This POC never emits
``waiting``.

Safety
======
Files are opened read-only and only a bounded tail is read. No prompt,
assistant, tool input, or tool output text is printed; only event-type markers
and timestamps are used.

Examples
========
    python scripts/poc/codex/02_codex_desktop_state_from_rollout.py
    python scripts/poc/codex/02_codex_desktop_state_from_rollout.py --json
    python scripts/poc/codex/02_codex_desktop_state_from_rollout.py \
        --rollout ~/.codex/sessions/2026/07/09/rollout-...jsonl

Interpretation
==============
``STATE_DERIVED_PERSISTED`` means a lifecycle marker was found and mapped at
persisted confidence. It does not prove real-time accuracy, does not detect the
waiting state, and is superseded by any fresh live hook observation.

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
DEFAULT_TAIL_BYTES = 256 * 1024
LIFECYCLE_MARKERS = {"task_started", "task_complete", "turn_aborted"}
MARKER_STATE = {
    "task_started": "working",
    "task_complete": "done",
    "turn_aborted": "idle",
}


class SchemaError(RuntimeError):
    """An undocumented Codex rollout record no longer matches observations."""


@dataclass(frozen=True)
class StateResult:
    rollout_path: str
    native_id: str | None
    marker: str | None
    state: str
    marker_at_ms: int | None
    confidence: str
    verdict: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only Codex Desktop state derivation from rollout tails.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--codex-root",
        type=Path,
        default=DEFAULT_CODEX_ROOT,
        help=f"Codex home directory (default: {DEFAULT_CODEX_ROOT})",
    )
    parser.add_argument(
        "--rollout",
        type=Path,
        default=None,
        help="Derive state for a single rollout file instead of scanning.",
    )
    parser.add_argument(
        "--tail-bytes",
        type=int,
        default=DEFAULT_TAIL_BYTES,
        help=f"Tail window scanned per rollout (default: {DEFAULT_TAIL_BYTES}).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum sessions to report (default: 20; 0 means unlimited).",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.tail_bytes <= 0:
        parser.error("--tail-bytes must be positive")
    if args.limit < 0:
        parser.error("--limit must be zero or positive")
    return args


def parse_iso_ms(value: Any) -> int | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return int(moment.timestamp() * 1000)


def read_first_meta(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("rb") as source:
            raw = source.readline(1024 * 1024)
    except OSError as error:
        raise SchemaError(f"{path}: cannot read rollout: {error}") from error
    if not raw.strip():
        return None
    try:
        record = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(record, dict) or record.get("type") != "session_meta":
        return None
    payload = record.get("payload")
    return payload if isinstance(payload, dict) else None


def is_desktop_user(payload: dict[str, Any]) -> bool:
    return (
        payload.get("originator") == DESKTOP_ORIGINATOR
        and payload.get("thread_source") == DESKTOP_THREAD_SOURCE
    )


def native_id(payload: dict[str, Any]) -> str | None:
    for key in ("session_id", "id"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def read_tail_lines(path: Path, tail_bytes: int) -> list[str]:
    """Return complete JSON lines from a bounded tail window."""

    try:
        size = path.stat().st_size
        with path.open("rb") as source:
            if size > tail_bytes:
                source.seek(size - tail_bytes)
            chunk = source.read()
    except OSError as error:
        raise SchemaError(f"{path}: cannot read tail: {error}") from error
    text = chunk.decode("utf-8", errors="replace")
    lines = text.split("\n")
    if size > tail_bytes and lines:
        # Drop the first, possibly partial, line.
        lines = lines[1:]
    return [line for line in lines if line.strip()]


def derive_state(path: Path, tail_bytes: int) -> StateResult:
    """Derive the newest lifecycle state from a rollout tail."""

    meta = read_first_meta(path)
    identity = native_id(meta) if meta else None
    marker: str | None = None
    marker_at_ms: int | None = None

    for line in read_tail_lines(path, tail_bytes):
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict) or record.get("type") != "event_msg":
            continue
        payload = record.get("payload")
        if not isinstance(payload, dict):
            continue
        event_type = payload.get("type")
        if event_type in LIFECYCLE_MARKERS:
            marker = event_type
            marker_at_ms = parse_iso_ms(record.get("timestamp"))

    if marker is None:
        state = "idle"
        confidence = "persisted"
        verdict = "STATE_UNKNOWN_NO_MARKER"
    else:
        state = MARKER_STATE[marker]
        confidence = "persisted"
        verdict = "STATE_DERIVED_PERSISTED"

    return StateResult(
        rollout_path=str(path),
        native_id=identity,
        marker=marker,
        state=state,
        marker_at_ms=marker_at_ms,
        confidence=confidence,
        verdict=verdict,
    )


def newest_desktop_rollouts(codex_root: Path) -> list[Path]:
    sessions_root = codex_root / "sessions"
    if not sessions_root.is_dir():
        raise FileNotFoundError(f"Codex sessions root not found: {sessions_root}")
    rollouts = sorted(sessions_root.glob("*/*/*/rollout-*.jsonl"))
    desktop: list[tuple[Path, dict[str, Any]]] = []
    for path in rollouts:
        meta = read_first_meta(path)
        if meta and is_desktop_user(meta):
            desktop.append((path, meta))
    desktop.sort(key=lambda item: item[0].stat().st_mtime, reverse=True)
    # Deduplicate resumed threads by native id, keeping the newest rollout, so a
    # `--limit` slice cannot hide a distinct session behind an older rollout of an
    # already-seen thread (mirrors the provider's dedup).
    seen: set[str] = set()
    deduped: list[Path] = []
    for path, meta in desktop:
        identity = native_id(meta)
        if identity is not None:
            if identity in seen:
                continue
            seen.add(identity)
        deduped.append(path)
    return deduped


def main() -> int:
    args = parse_args()
    try:
        if args.rollout is not None:
            results = [derive_state(args.rollout, args.tail_bytes)]
        else:
            paths = newest_desktop_rollouts(args.codex_root)
            if args.limit:
                paths = paths[: args.limit]
            results = [derive_state(path, args.tail_bytes) for path in paths]
    except (FileNotFoundError, SchemaError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps([asdict(result) for result in results], indent=2, sort_keys=True))
    else:
        print("Codex Desktop rollout-tail state derivation")
        print("Waiting (orange) is NOT derivable from rollouts; requires live hooks.")
        for result in results:
            print(
                f"{result.native_id or '<no-id>'}: state={result.state} "
                f"marker={result.marker or '<none>'} "
                f"confidence={result.confidence} verdict={result.verdict}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
