#!/usr/bin/env python3
"""POC V3.2: safely record and analyze official Claude Code hook events.

Purpose
=======
Establish whether official Claude Code hooks provide enough evidence for the
deck's blue, orange, green, and gray state model. This POC deliberately keeps
hook evidence separate from the undocumented local session inventory tested by
``01_claude_code_session_inventory.py``.

Method
======
The ``record`` command reads one official hook payload from standard input,
validates its common identity fields, removes prompt and assistant content, and
appends sanitized metadata to a dedicated JSONL evidence file. The ``analyze``
command reports observed event coverage and conservative state conclusions.
The ``config`` command prints, but never installs, a settings snippet for:

* ``SessionStart``
* ``UserPromptSubmit``
* ``PreToolUse`` and ``PostToolUse`` for interactive question tools
* ``PostToolBatch``
* ``PermissionRequest``
* ``PermissionDenied``
* ``Notification``
* ``Elicitation`` and ``ElicitationResult``
* ``SubagentStart`` and ``SubagentStop``
* ``PreCompact`` and ``PostCompact``
* ``Stop``
* ``StopFailure``
* ``SessionEnd``

Claude Code also supports native HTTP hook handlers. That is a promising
production transport because it avoids a shell-command dependency, but this
POC uses a local file so evidence remains inspectable and reproducible.

Safety and side effects
=======================
``config`` and ``analyze`` are read-only. ``record`` has one explicit side
effect: it creates or appends to the file passed with ``--log``. It never
changes Claude settings, blocks an event, controls a session, or stores prompt
text, assistant text, tool inputs, or tool outputs. The ``record`` command
always exits zero, including when evidence cannot be written, so this
diagnostic hook cannot intentionally block Claude Code.

Examples
========
    python scripts/poc/claude/02_claude_code_hook_probe.py config \\
      --log /tmp/elchango-claude-hooks.jsonl
    printf '%s' '{"session_id":"s1","cwd":"/tmp","transcript_path":"/tmp/s1.jsonl","hook_event_name":"Stop","stop_hook_active":false}' \\
      | python scripts/poc/claude/02_claude_code_hook_probe.py record \\
          --log /tmp/elchango-claude-hooks.jsonl
    python scripts/poc/claude/02_claude_code_hook_probe.py analyze \\
      --log /tmp/elchango-claude-hooks.jsonl

Interpretation
==============
Observed ``UserPromptSubmit`` supports blue (working). ``PreToolUse`` for
``AskUserQuestion`` or ``ExitPlanMode``, ``PermissionRequest``, and
``Elicitation`` support orange (waiting). Their corresponding completion events
support a return to blue. ``Stop`` supports green (done). ``StopFailure``
supports an explicit error/degraded terminal state. ``Notification`` remains an
additional orange signal for documented waiting notification types.
``SessionStart`` and ``SessionEnd`` describe lifecycle boundaries, not active
turn state. Missing terminal evidence must become degraded after a freshness
deadline; it must never be invented from transcript contents.

Official reference
==================
https://docs.anthropic.com/en/docs/claude-code/hooks
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


OBSERVED_EVENTS = (
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "PostToolBatch",
    "PermissionRequest",
    "PermissionDenied",
    "Notification",
    "Elicitation",
    "ElicitationResult",
    "SubagentStart",
    "SubagentStop",
    "PreCompact",
    "PostCompact",
    "Stop",
    "StopFailure",
    "SessionEnd",
)
WAITING_NOTIFICATIONS = {
    "permission_prompt",
    "elicitation_dialog",
    "agent_needs_input",
}
WAITING_TOOLS = {"AskUserQuestion", "ExitPlanMode"}
TERMINAL_EVENTS = {"Stop", "StopFailure"}


class ProbeError(RuntimeError):
    """Hook input or recorded evidence is invalid."""


@dataclass(frozen=True)
class HookRecord:
    """Sanitized metadata retained from one official hook payload."""

    observed_at_ms: int
    event: str
    session_id: str
    cwd: str
    transcript_path: str
    notification_type: str | None
    tool_name: str | None
    prompt_id: str | None
    permission_mode: str | None
    source: str | None
    agent_id: str | None
    compact_trigger: str | None
    background_tasks: tuple[dict[str, str], ...]
    stop_hook_active: bool | None


@dataclass(frozen=True)
class Analysis:
    """Observed facts and deliberately conservative capability verdicts."""

    records: int
    sessions: int
    event_counts: dict[str, int]
    notification_counts: dict[str, int]
    sessions_with_submit_and_terminal: int
    sessions_with_waiting_notification: int
    working_verdict: str
    waiting_verdict: str
    terminal_verdict: str
    lifecycle_verdict: str
    state_verdict: str
    targeting_verdict: str
    observations: tuple[str, ...]
    conclusions: tuple[str, ...]
    hypotheses: tuple[str, ...]


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        description="Record and analyze sanitized Claude Code hook evidence.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    commands = root.add_subparsers(dest="command", required=True)

    record = commands.add_parser(
        "record",
        help="Read one hook JSON payload from stdin and append sanitized evidence.",
    )
    record.add_argument("--log", type=Path, required=True)

    analyze = commands.add_parser(
        "analyze",
        help="Analyze a previously recorded JSONL evidence file.",
    )
    analyze.add_argument("--log", type=Path, required=True)
    analyze.add_argument("--json", action="store_true")

    config = commands.add_parser(
        "config",
        help="Print a Claude settings hook snippet without installing it.",
    )
    config.add_argument("--log", type=Path, required=True)
    config.add_argument(
        "--python",
        default=sys.executable,
        help=f"Python executable in generated commands (default: {sys.executable})",
    )
    config.add_argument(
        "--script",
        type=Path,
        default=Path(__file__).resolve(),
        help="Absolute path to this probe script.",
    )
    return root


def required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ProbeError(f"{key} must be a non-empty string")
    return value


def optional_string(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is not None and (not isinstance(value, str) or not value):
        raise ProbeError(f"{key} must be a non-empty string or null")
    return value


def sanitized_background_tasks(payload: dict[str, Any]) -> tuple[dict[str, str], ...]:
    value = payload.get("background_tasks")
    if value is None:
        return ()
    if not isinstance(value, list) or len(value) > 64:
        raise ProbeError("background_tasks must be a bounded array")
    tasks: list[dict[str, str]] = []
    for task in value:
        if not isinstance(task, dict):
            raise ProbeError("background task metadata must be an object")
        metadata = {
            key: field
            for key in ("id", "type", "status", "agent_type")
            if isinstance((field := task.get(key)), str)
            and 1 <= len(field.encode()) <= 256
        }
        tasks.append(metadata)
    return tuple(tasks)


def sanitize_hook_payload(payload: Any) -> HookRecord:
    """Validate common official fields and retain no conversational content."""

    if not isinstance(payload, dict):
        raise ProbeError("hook payload must be a JSON object")
    event = required_string(payload, "hook_event_name")
    if event not in OBSERVED_EVENTS:
        raise ProbeError(f"unsupported hook_event_name: {event}")
    stop_hook_active = payload.get("stop_hook_active")
    if stop_hook_active is not None and not isinstance(stop_hook_active, bool):
        raise ProbeError("stop_hook_active must be a boolean or null")
    return HookRecord(
        observed_at_ms=time.time_ns() // 1_000_000,
        event=event,
        session_id=required_string(payload, "session_id"),
        cwd=required_string(payload, "cwd"),
        transcript_path=required_string(payload, "transcript_path"),
        notification_type=optional_string(payload, "notification_type"),
        tool_name=optional_string(payload, "tool_name"),
        prompt_id=optional_string(payload, "prompt_id"),
        permission_mode=optional_string(payload, "permission_mode"),
        source=optional_string(payload, "source"),
        agent_id=optional_string(payload, "agent_id"),
        compact_trigger=optional_string(payload, "trigger"),
        background_tasks=sanitized_background_tasks(payload),
        stop_hook_active=stop_hook_active,
    )


def append_record(path: Path, record: HookRecord) -> None:
    """Append exactly one compact JSON record with owner-only permissions."""

    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(asdict(record), separators=(",", ":"), sort_keys=True) + "\n"
    descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(descriptor, line.encode("utf-8"))
    finally:
        os.close(descriptor)


def load_records(path: Path) -> list[HookRecord]:
    """Load records while failing explicitly on partial or unknown evidence."""

    if not path.is_file():
        raise FileNotFoundError(f"hook evidence file not found: {path}")
    records: list[HookRecord] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
            if not isinstance(value, dict):
                raise TypeError("evidence record must be an object")
            normalized = dict(value)
            normalized.pop("reason", None)
            normalized.pop("error", None)
            for key in (
                "prompt_id",
                "permission_mode",
                "source",
                "agent_id",
                "compact_trigger",
            ):
                normalized.setdefault(key, None)
            normalized.setdefault("background_tasks", [])
            normalized.setdefault("stop_hook_active", None)
            allowed = set(HookRecord.__dataclass_fields__)
            unknown = set(normalized) - allowed
            if unknown:
                raise TypeError(
                    f"unknown evidence fields: {sorted(unknown)}"
                )
            background_tasks = normalized["background_tasks"]
            if not isinstance(background_tasks, (list, tuple)):
                raise TypeError("background_tasks must be an array")
            normalized["background_tasks"] = tuple(background_tasks)
            records.append(HookRecord(**normalized))
        except (json.JSONDecodeError, TypeError) as error:
            raise ProbeError(f"{path}:{line_number}: invalid evidence: {error}") from error
    return records


def analyze_records(records: list[HookRecord]) -> Analysis:
    event_counts = Counter(record.event for record in records)
    notification_counts = Counter(
        record.notification_type
        for record in records
        if record.event == "Notification" and record.notification_type is not None
    )
    by_session: dict[str, set[str]] = defaultdict(set)
    waiting_sessions: set[str] = set()
    for record in records:
        by_session[record.session_id].add(record.event)
        if (
            record.event == "Notification"
            and record.notification_type in WAITING_NOTIFICATIONS
        ) or record.event in {"PermissionRequest", "Elicitation"} or (
            record.event == "PreToolUse" and record.tool_name in WAITING_TOOLS
        ):
            waiting_sessions.add(record.session_id)
    submit_and_terminal = sum(
        "UserPromptSubmit" in events and bool(events & TERMINAL_EVENTS)
        for events in by_session.values()
    )

    working_observed = event_counts["UserPromptSubmit"] > 0
    waiting_observed = bool(waiting_sessions)
    terminal_observed = bool(
        event_counts["Stop"] or event_counts["StopFailure"]
    )
    lifecycle_observed = bool(
        event_counts["SessionStart"] and event_counts["SessionEnd"]
    )
    state_supported = (
        working_observed and waiting_observed and terminal_observed
    )
    observations = tuple(
        f"{event}: {event_counts[event]}"
        for event in OBSERVED_EVENTS
        if event_counts[event]
    )
    conclusions = (
        "UserPromptSubmit is direct evidence that a user turn was submitted."
        if working_observed
        else "No working transition has been observed.",
        "A documented waiting notification type was observed."
        if waiting_observed
        else "Orange waiting state remains unproven without a waiting notification.",
        "A documented terminal turn event was observed."
        if terminal_observed
        else "Green/error terminal state remains unproven without Stop or StopFailure.",
        "Hook evidence contains session_id, cwd, and transcript_path for correlation."
        if records
        else "No hook identity fields have been observed.",
    )
    hypotheses = (
        "Native HTTP hooks can likely report directly to the loopback elChango server.",
        "A freshness deadline can mark a missing terminal event as degraded.",
        "Hook session_id should match the local process registry sessionId, but this "
        "must be checked from observed records.",
    )
    return Analysis(
        records=len(records),
        sessions=len(by_session),
        event_counts=dict(sorted(event_counts.items())),
        notification_counts=dict(sorted(notification_counts.items())),
        sessions_with_submit_and_terminal=submit_and_terminal,
        sessions_with_waiting_notification=len(waiting_sessions),
        working_verdict="OBSERVED" if working_observed else "UNPROVEN",
        waiting_verdict="OBSERVED" if waiting_observed else "UNPROVEN",
        terminal_verdict="OBSERVED" if terminal_observed else "UNPROVEN",
        lifecycle_verdict="OBSERVED" if lifecycle_observed else "PARTIAL_OR_UNPROVEN",
        state_verdict="SUPPORTED_CANDIDATE" if state_supported else "INCOMPLETE_EVIDENCE",
        targeting_verdict="UNPROVEN_NO_FOCUS_ACTION_PERFORMED",
        observations=observations,
        conclusions=conclusions,
        hypotheses=hypotheses,
    )


def hook_config(python: str, script: Path, log: Path) -> dict[str, Any]:
    command = " ".join(
        shlex.quote(part)
        for part in (
            python,
            str(script.resolve()),
            "record",
            "--log",
            str(log.resolve()),
        )
    )
    handler = [{"hooks": [{"type": "command", "command": command, "timeout": 10}]}]
    hooks = {event: handler for event in OBSERVED_EVENTS}
    hooks["PreToolUse"] = [
        {"matcher": "AskUserQuestion|ExitPlanMode", **handler[0]}
    ]
    hooks["PostToolUse"] = [
        {"matcher": "AskUserQuestion|ExitPlanMode", **handler[0]}
    ]
    hooks["Notification"] = [
        {
            "matcher": (
                "permission_prompt|idle_prompt|elicitation_dialog|"
                "elicitation_complete|elicitation_response|agent_needs_input|"
                "agent_completed"
            ),
            **handler[0],
        }
    ]
    for event in ("PreCompact", "PostCompact"):
        hooks[event] = [{"matcher": "manual|auto", **handler[0]}]
    return {"hooks": hooks}


def print_analysis(analysis: Analysis) -> None:
    print("Claude Code hook evidence")
    print(f"Records: {analysis.records}; sessions: {analysis.sessions}")
    print(
        f"Working: {analysis.working_verdict}; "
        f"waiting: {analysis.waiting_verdict}; "
        f"terminal: {analysis.terminal_verdict}; "
        f"lifecycle: {analysis.lifecycle_verdict}"
    )
    print(f"State verdict: {analysis.state_verdict}")
    print(f"Targeting verdict: {analysis.targeting_verdict}")
    print("Observations:")
    for observation in analysis.observations or ("No events recorded.",):
        print(f"- {observation}")
    print("Conclusions:")
    for conclusion in analysis.conclusions:
        print(f"- {conclusion}")
    print("Unproven hypotheses:")
    for hypothesis in analysis.hypotheses:
        print(f"- {hypothesis}")


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "record":
            try:
                payload = json.load(sys.stdin)
            except json.JSONDecodeError as error:
                raise ProbeError(f"stdin is not valid JSON: {error}") from error
            append_record(args.log, sanitize_hook_payload(payload))
            return 0
        if args.command == "config":
            print(json.dumps(hook_config(args.python, args.script, args.log), indent=2))
            return 0
        records = load_records(args.log)
        analysis = analyze_records(records)
        if args.json:
            print(json.dumps(asdict(analysis), indent=2, sort_keys=True))
        else:
            print_analysis(analysis)
        return 0
    except (FileNotFoundError, OSError, ProbeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 0 if args.command == "record" else 2


if __name__ == "__main__":
    raise SystemExit(main())
