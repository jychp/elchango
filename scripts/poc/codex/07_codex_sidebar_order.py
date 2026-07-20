#!/usr/bin/env python3
"""POC 07: reconstruct the Codex Desktop sidebar order.

Purpose
=======
Codex Desktop presents sessions in the sidebar as:

1. pinned threads first;
2. then projects, each ordered by last activity; within a project, threads
   follow a manual order when the user has reordered them, otherwise last
   activity;
3. then loose (projectless) threads by last activity.

A verifiable existing-session focus (as the Claude provider does with sidebar
position shortcuts) needs this order. This POC reconstructs it read-only from
persisted state, so a future focus mechanism has a defensible target order.

Method and evidence
===================
The order is persisted in ``~/.codex/.codex-global-state.json``:

- ``pinned-thread-ids``: pinned thread ids, in sidebar order;
- ``local-projects``: ``{projectId: {id, name, rootPaths, createdAt, updatedAt}}``;
- ``selected-project``: ``{type, projectId}`` (selected project, not thread);
- ``thread-project-assignments``: ``{threadId: {projectKind, projectId, cwd}}``;
- ``sidebar-project-thread-orders``: ``{projectId: {threadIds: [...]}}`` manual
  order overrides;
- ``projectless-thread-ids``: loose threads.

Last activity per thread comes from the rollout files (bounded first-line and
mtime), reusing the same Desktop-user filter as POC 01. Projects are ordered by
the newest last activity among their threads.

Observation, not conclusion: ``selected-project`` is persisted, but no selected
or active *thread* id was found in this file. The active session is not
statically resolvable here.

Safety
======
Read-only. Only ``.codex-global-state.json`` and bounded rollout prefixes are
read; no thread content is printed unless ``--show-titles`` is given.

Examples
========
    python scripts/poc/codex/07_codex_sidebar_order.py
    python scripts/poc/codex/07_codex_sidebar_order.py --json
    python scripts/poc/codex/07_codex_sidebar_order.py \
        --codex-root contracts/providers/codex/v1

Interpretation
==============
``SIDEBAR_ORDER_RECONSTRUCTED``: the order was rebuilt from persisted state. It
does not prove a focus mechanism: no sidebar-position keyboard shortcut is known
for Codex Desktop, and no selected-thread signal exists to verify a focus after
acting. Focus stays rejected (fail closed) until those are established live.

Official references
===================
https://developers.openai.com/codex/
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_CODEX_ROOT = Path.home() / ".codex"
DESKTOP_ORIGINATOR = "Codex Desktop"
DESKTOP_THREAD_SOURCE = "user"


@dataclass(frozen=True)
class Entry:
    thread_id: str
    group: str
    is_desktop: bool
    last_activity_at_ms: int


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


def desktop_last_activity(codex_root: Path) -> dict[str, int]:
    """Map native thread id -> newest last activity for Desktop user rollouts."""

    root = codex_root / "sessions"
    activity: dict[str, int] = {}
    if not root.is_dir():
        return activity
    for path in root.glob("*/*/*/rollout-*.jsonl"):
        try:
            with path.open("rb") as source:
                raw = source.readline(1024 * 1024)
        except OSError:
            continue
        if not raw.strip():
            continue
        try:
            record = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(record, dict) or record.get("type") != "session_meta":
            continue
        payload = record.get("payload")
        if not isinstance(payload, dict):
            continue
        if (
            payload.get("originator") != DESKTOP_ORIGINATOR
            or payload.get("thread_source") != DESKTOP_THREAD_SOURCE
        ):
            continue
        native = next(
            (
                payload[key]
                for key in ("session_id", "id")
                if isinstance(payload.get(key), str) and payload[key]
            ),
            None,
        )
        if native is None:
            continue
        meta_ms = parse_iso_ms(payload.get("timestamp")) or 0
        mtime_ms = int(path.stat().st_mtime * 1000)
        activity[native] = max(activity.get(native, 0), meta_ms, mtime_ms)
    return activity


def reconstruct(codex_root: Path) -> tuple[list[Entry], dict[str, Any]]:
    state_path = codex_root / ".codex-global-state.json"
    if not state_path.is_file():
        raise FileNotFoundError(f"Codex global state not found: {state_path}")
    state = json.loads(state_path.read_text(encoding="utf-8"))

    pinned = [t for t in state.get("pinned-thread-ids", []) if isinstance(t, str)]
    projectless = [
        t for t in state.get("projectless-thread-ids", []) if isinstance(t, str)
    ]
    projects = state.get("local-projects", {})
    assignments = state.get("thread-project-assignments", {})
    manual_orders = state.get("sidebar-project-thread-orders", {})
    activity = desktop_last_activity(codex_root)
    placed: set[str] = set()
    order: list[Entry] = []

    def add(thread_id: str, group: str) -> None:
        if thread_id in placed:
            return
        # Provider scope is Desktop-user sessions only. CLI and subagent ids can
        # appear in global sidebar state; skip any id without a Desktop-user
        # rollout so the reconstructed order never includes out-of-scope sessions.
        if thread_id not in activity:
            return
        placed.add(thread_id)
        order.append(
            Entry(
                thread_id=thread_id,
                group=group,
                is_desktop=True,
                last_activity_at_ms=activity[thread_id],
            )
        )

    for thread_id in pinned:
        add(thread_id, "pinned")

    # Threads assigned to each project.
    threads_by_project: dict[str, list[str]] = {}
    for thread_id, meta in assignments.items():
        if not isinstance(meta, dict):
            continue
        project_id = meta.get("projectId")
        if isinstance(project_id, str):
            threads_by_project.setdefault(project_id, []).append(thread_id)

    def project_activity(project_id: str) -> int:
        return max(
            (activity.get(t, 0) for t in threads_by_project.get(project_id, [])),
            default=0,
        )

    for project_id in sorted(
        projects, key=lambda p: project_activity(p), reverse=True
    ):
        manual = manual_orders.get(project_id, {})
        manual_ids = manual.get("threadIds", []) if isinstance(manual, dict) else []
        remaining = [
            t
            for t in threads_by_project.get(project_id, [])
            if t not in manual_ids
        ]
        remaining.sort(key=lambda t: activity.get(t, 0), reverse=True)
        for thread_id in list(manual_ids) + remaining:
            if isinstance(thread_id, str):
                add(thread_id, f"project:{project_id}")

    for thread_id in sorted(
        projectless, key=lambda t: activity.get(t, 0), reverse=True
    ):
        add(thread_id, "projectless")

    selected_project = state.get("selected-project")
    summary = {
        "selected_project": selected_project,
        "selected_thread": None,  # not persisted in this file
        "pinned_count": len(pinned),
        "project_count": len(projects),
        "verdict": "SIDEBAR_ORDER_RECONSTRUCTED",
    }
    return order, summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only Codex Desktop sidebar-order reconstruction.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--codex-root", type=Path, default=DEFAULT_CODEX_ROOT)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        order, summary = reconstruct(args.codex_root)
    except (FileNotFoundError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2

    if args.json:
        print(
            json.dumps(
                {
                    "order": [entry.__dict__ for entry in order],
                    "summary": summary,
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print("Codex Desktop sidebar order")
        print(f"Verdict: {summary['verdict']}")
        print(
            f"Selected project: {summary['selected_project']}; "
            f"selected thread: not persisted in global state"
        )
        for index, entry in enumerate(order, start=1):
            print(
                f"{index:>2}. {entry.thread_id} "
                f"[{entry.group}] desktop={entry.is_desktop}"
            )
        print("Limitations:")
        print("- Scoped to Desktop-user sessions; CLI/subagent ids are excluded.")
        print("- Project ordering uses rollout last activity as the signal.")
        print("- No sidebar-position focus shortcut is known for Codex Desktop.")
        print("- No selected-thread signal exists to verify a focus after acting.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
