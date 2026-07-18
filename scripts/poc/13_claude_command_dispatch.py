#!/usr/bin/env python3
"""POC V4.2: conservatively dispatch a personalized command to Claude Code.

Purpose
=======
Measure whether elChango can send one semantic command to one exact, already
selected Claude Desktop Code session without inventing a provider mapping.

The modeled semantic IDs are ``accept``, ``create_pr``, ``commit_push``, and
``compact``. No slash command, shortcut, or prompt is claimed to be official.
The operator must supply the exact recipe with ``--recipe-text`` or
``--recipe-command``.

Method
======
The POC reads bounded metadata prefixes from Claude Desktop's persistent Code
records. It verifies:

1. the exact Desktop ``local_<uuid>`` target is non-archived;
2. its ``lastFocusedAt`` is present and uniquely newest among visible sessions;
3. Claude Desktop is the frontmost application;
4. macOS Accessibility reports a focused, enabled text input whose metadata
   contains the operator-supplied ``--input-marker``;
5. all evidence is unchanged in an immediate second preflight.

Only then does execute mode type the supplied recipe. Text recipes press Return
once. Slash-command recipes wait for Claude's suggestion UI, press Return to
select the command, wait again, and press Return to submit it. There is no
focus action, coordinate click, mapping fallback, or retry.

Safety and side effects
=======================
Default mode is read-only and dry-run. ``--execute`` is required to inject
text. Execute mode changes the target conversation by submitting exactly one
operator-supplied recipe. The explicit input marker prevents application focus
from being mistaken for Code prompt focus.

Examples
========
    python scripts/poc/13_claude_command_dispatch.py
    python scripts/poc/13_claude_command_dispatch.py accept --target local_<uuid>
    python scripts/poc/13_claude_command_dispatch.py compact \
      --target local_<uuid> --recipe-command /known-command \
      --input-marker code-prompt
    python scripts/poc/13_claude_command_dispatch.py commit_push \
      --target local_<uuid> --recipe-text "Commit and push..." \
      --input-marker code-prompt --execute

Interpretation
==============
``DRY_RUN`` means no input was sent. ``READY`` means current preflight evidence
passed. ``DISPATCH_SENT`` means one recipe was submitted and the target remained
uniquely selected immediately afterward. It does not prove Claude understood or
completed the semantic command. Refusal verdicts mean no input was sent and
must not trigger an automatic retry.

Limitations
===========
Claude Desktop record fields and accessibility metadata are implementation
details. An input marker must come from direct observation on the installed
version. This probe cannot prove command completion or official command support.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


DEFAULT_DESKTOP_ROOT = (
    Path.home() / "Library/Application Support/Claude/claude-code-sessions"
)
SEMANTIC_IDS = ("accept", "create_pr", "commit_push", "compact")
TEXT_ROLES = {"AXTextArea", "AXTextField"}
CLAUDE_BUNDLE_ID = "com.anthropic.claudefordesktop"
MAX_METADATA_PREFIX_BYTES = 64 * 1024
REQUIRED_FIELDS = {
    "sessionId",
    "cliSessionId",
    "lastActivityAt",
    "isArchived",
}
OPTIONAL_FIELDS = {"lastFocusedAt", "title"}


class ProbeError(RuntimeError):
    """The local schema or targeting evidence is unsafe."""


@dataclass(frozen=True, slots=True)
class Session:
    session_id: str
    cli_session_id: str
    last_activity_at_ms: int
    last_focused_at_ms: int | None
    archived: bool


@dataclass(frozen=True, slots=True)
class FocusEvidence:
    bundle_id: str | None
    role: str | None
    identifier: str | None
    description: str | None
    title: str | None
    help_text: str | None
    dom_class: str | None
    enabled: bool | None


@dataclass(frozen=True, slots=True)
class DispatchResult:
    verdict: str
    message: str
    semantic_id: str | None
    target: str | None
    recipe_kind: str | None
    executed: bool
    selected_before: str | None
    selected_after: str | None
    focus: FocusEvidence


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Probe exact-target Claude Code command dispatch.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Dry-run is the default. No semantic ID has an official recipe in\n"
            "this POC. --execute sends one explicitly supplied recipe only."
        ),
    )
    parser.add_argument("semantic_id", nargs="?", choices=SEMANTIC_IDS)
    parser.add_argument("--target", help="Exact Desktop local_<uuid> session ID.")
    recipes = parser.add_mutually_exclusive_group()
    recipes.add_argument(
        "--recipe-text",
        help="Exact natural-language recipe to submit; never sourced from a surface.",
    )
    recipes.add_argument(
        "--recipe-command",
        help="Exact provider command recipe to submit; no mapping is inferred.",
    )
    parser.add_argument(
        "--input-marker",
        help="Exact value expected in one focused-input accessibility field.",
    )
    parser.add_argument(
        "--desktop-sessions-root",
        type=Path,
        default=DEFAULT_DESKTOP_ROOT,
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Submit the supplied recipe once after two exact preflights.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON.")
    args = parser.parse_args()
    if args.execute:
        missing = [
            option
            for option, value in (
                ("semantic_id", args.semantic_id),
                ("--target", args.target),
                ("--recipe-text or --recipe-command", recipe(args)[1]),
                ("--input-marker", args.input_marker),
            )
            if not value
        ]
        if missing:
            parser.error("--execute requires " + ", ".join(missing))
    return args


def recipe(args: argparse.Namespace) -> tuple[str | None, str | None]:
    if args.recipe_text is not None:
        return "text", args.recipe_text
    if args.recipe_command is not None:
        return "command", args.recipe_command
    return None, None


def inventory(root: Path) -> list[Session]:
    if not root.is_dir():
        raise ProbeError(f"Claude Desktop session root not found: {root}")
    sessions: list[Session] = []
    for path in sorted(root.glob("*/*/local_*.json")):
        value = read_metadata(path)
        session_id = required_string(value, "sessionId", path)
        if path.stem != session_id:
            raise ProbeError(f"{path}: filename does not match sessionId")
        archived = value.get("isArchived")
        if not isinstance(archived, bool):
            raise ProbeError(f"{path}: isArchived must be a boolean")
        focused = value.get("lastFocusedAt")
        if focused is not None:
            focused = integer(focused, "lastFocusedAt", path)
        sessions.append(
            Session(
                session_id,
                required_string(value, "cliSessionId", path),
                integer(value.get("lastActivityAt"), "lastActivityAt", path),
                focused,
                archived,
            )
        )
    return sessions


def read_metadata(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as source:
            prefix = source.read(MAX_METADATA_PREFIX_BYTES)
    except (OSError, UnicodeDecodeError) as error:
        raise ProbeError(f"{path}: cannot read metadata: {error}") from error
    if not prefix.startswith("{"):
        raise ProbeError(f"{path}: record must be a JSON object")
    decoder = json.JSONDecoder()
    values: dict[str, Any] = {}
    position = 1
    try:
        while True:
            position = skip_whitespace(prefix, position)
            key, position = decoder.raw_decode(prefix, position)
            if not isinstance(key, str):
                raise ProbeError(f"{path}: field name must be a string")
            if not (REQUIRED_FIELDS - values.keys()) and key not in OPTIONAL_FIELDS:
                break
            position = skip_whitespace(prefix, position)
            if position >= len(prefix) or prefix[position] != ":":
                raise ProbeError(f"{path}: missing colon after {key}")
            position = skip_whitespace(prefix, position + 1)
            value, position = decoder.raw_decode(prefix, position)
            if key in REQUIRED_FIELDS | OPTIONAL_FIELDS:
                values[key] = value
            position = skip_whitespace(prefix, position)
            if position >= len(prefix) or prefix[position] not in {",", "}"}:
                raise ProbeError(f"{path}: invalid separator after {key}")
            if prefix[position] == "}":
                break
            position += 1
    except json.JSONDecodeError as error:
        raise ProbeError(f"{path}: invalid bounded metadata prefix") from error
    missing = REQUIRED_FIELDS - values.keys()
    if missing:
        raise ProbeError(f"{path}: missing required fields: {sorted(missing)}")
    return values


def skip_whitespace(text: str, position: int) -> int:
    while position < len(text) and text[position].isspace():
        position += 1
    return position


def required_string(value: dict[str, Any], key: str, path: Path) -> str:
    candidate = value.get(key)
    if not isinstance(candidate, str) or not candidate:
        raise ProbeError(f"{path}: {key} must be a non-empty string")
    return candidate


def integer(value: object, key: str, path: Path) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ProbeError(f"{path}: {key} must be a non-negative integer")
    return value


def exact_selected_id(sessions: list[Session]) -> str | None:
    visible = [session for session in sessions if not session.archived]
    timestamps = [
        session.last_focused_at_ms
        for session in visible
        if session.last_focused_at_ms is not None
    ]
    if not timestamps:
        return None
    newest = max(timestamps)
    selected = [
        session.session_id
        for session in visible
        if session.last_focused_at_ms == newest
    ]
    return selected[0] if len(selected) == 1 else None


def validate_target(sessions: list[Session], target: str) -> None:
    matches = [session for session in sessions if session.session_id == target]
    if len(matches) != 1:
        raise ProbeError(f"Expected one exact target record, found {len(matches)}")
    if matches[0].archived:
        raise ProbeError("Target Claude Desktop session is archived.")
    if matches[0].last_focused_at_ms is None:
        raise ProbeError("Target has no lastFocusedAt evidence.")


def accessibility_focus() -> FocusEvidence:
    script = r'''
tell application "System Events"
  set p to first application process whose frontmost is true
  set bundleValue to ""
  try
    set bundleValue to bundle identifier of p
  end try
  set e to value of attribute "AXFocusedUIElement" of p
  set roleValue to my axValue(e, "AXRole")
  set idValue to my axValue(e, "AXIdentifier")
  set descriptionValue to my axValue(e, "AXDescription")
  set titleValue to my axValue(e, "AXTitle")
  set helpValue to my axValue(e, "AXHelp")
  set domClassValue to my axValue(e, "AXDOMClassList")
  set enabledValue to my axValue(e, "AXEnabled")
  return bundleValue & tab & roleValue & tab & idValue & tab & descriptionValue & tab & titleValue & tab & helpValue & tab & domClassValue & tab & enabledValue
end tell

on axValue(e, attributeName)
  tell application "System Events"
    try
      return (value of attribute attributeName of e) as text
    on error
      return ""
    end try
  end tell
end axValue
'''
    completed = subprocess.run(
        ["/usr/bin/osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=3,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or "Accessibility query failed."
        raise ProbeError(detail)
    fields = completed.stdout.rstrip("\n").split("\t")
    if len(fields) != 8:
        raise ProbeError("Unexpected Accessibility evidence shape.")
    enabled = {"true": True, "false": False}.get(fields[7].lower())
    values = [value or None for value in fields[:7]]
    return FocusEvidence(*values, enabled)


def focus_is_exact(focus: FocusEvidence, marker: str | None) -> bool:
    return bool(
        marker
        and focus.bundle_id == CLAUDE_BUNDLE_ID
        and focus.role in TEXT_ROLES
        and focus.enabled is True
        and marker
        in {
            focus.identifier,
            focus.description,
            focus.title,
            focus.help_text,
            focus.dom_class,
        }
    )


def send_once(value: str, *, confirm_suggestion: bool) -> None:
    script = r'''
on run argv
  tell application "System Events"
    keystroke (item 1 of argv)
    delay 0.5
    key code 36
    if (item 2 of argv) is "true" then
      delay 0.5
      key code 36
    end if
  end tell
end run
'''
    completed = subprocess.run(
        [
            "/usr/bin/osascript",
            "-e",
            script,
            "--",
            value,
            "true" if confirm_suggestion else "false",
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or "Input injection failed."
        raise ProbeError(detail)


def result(
    verdict: str,
    message: str,
    args: argparse.Namespace,
    recipe_kind: str | None,
    executed: bool,
    selected_before: str | None,
    selected_after: str | None,
    focus: FocusEvidence,
) -> DispatchResult:
    return DispatchResult(
        verdict,
        message,
        args.semantic_id,
        args.target,
        recipe_kind,
        executed,
        selected_before,
        selected_after,
        focus,
    )


def inspect(args: argparse.Namespace) -> DispatchResult:
    recipe_kind, recipe_value = recipe(args)
    sessions = inventory(args.desktop_sessions_root)
    before = exact_selected_id(sessions)
    focus = accessibility_focus()
    if args.target:
        validate_target(sessions, args.target)
    if not args.semantic_id or not args.target:
        return result(
            "DRY_RUN",
            "Supply a semantic ID and exact target to evaluate dispatch.",
            args,
            recipe_kind,
            False,
            before,
            before,
            focus,
        )
    if before != args.target:
        return result(
            "TARGET_NOT_SELECTED",
            "Exact target is not uniquely selected; no input sent.",
            args,
            recipe_kind,
            False,
            before,
            before,
            focus,
        )
    if not focus_is_exact(focus, args.input_marker):
        return result(
            "INPUT_FOCUS_NOT_VERIFIED",
            "Frontmost exact Code input evidence is insufficient; no input sent.",
            args,
            recipe_kind,
            False,
            before,
            before,
            focus,
        )
    if not recipe_value:
        return result(
            "RECIPE_REQUIRED",
            "No official mapping is assumed; supply an explicit recipe.",
            args,
            recipe_kind,
            False,
            before,
            before,
            focus,
        )
    if not args.execute:
        return result(
            "READY",
            "Current evidence passes; dry-run sent no input.",
            args,
            recipe_kind,
            False,
            before,
            before,
            focus,
        )

    latest_sessions = inventory(args.desktop_sessions_root)
    latest_selected = exact_selected_id(latest_sessions)
    latest_focus = accessibility_focus()
    if latest_selected != before or latest_focus != focus:
        return result(
            "STALE_PREFLIGHT",
            "Target or focused input changed before injection; no input sent.",
            args,
            recipe_kind,
            False,
            before,
            latest_selected,
            latest_focus,
        )
    send_once(recipe_value, confirm_suggestion=recipe_kind == "command")
    after = exact_selected_id(inventory(args.desktop_sessions_root))
    verdict = "DISPATCH_SENT" if after == args.target else "POST_DISPATCH_AMBIGUOUS"
    message = (
        "One recipe was submitted; semantic completion remains unverified."
        if verdict == "DISPATCH_SENT"
        else "One recipe was submitted, but exact target evidence changed."
    )
    return result(
        verdict,
        message,
        args,
        recipe_kind,
        True,
        before,
        after,
        latest_focus,
    )


def main() -> int:
    args = parse_args()
    try:
        observation = inspect(args)
        if args.json:
            print(json.dumps(asdict(observation), indent=2, sort_keys=True))
        else:
            print("Claude Code command-dispatch POC V4.2")
            print(f"VERDICT: {observation.verdict}")
            print(f"Semantic ID: {observation.semantic_id}")
            print(f"Target: {observation.target}")
            print(f"Recipe kind: {observation.recipe_kind}")
            print(f"Executed: {observation.executed}")
            print(
                "Selected before/after: "
                f"{observation.selected_before} / {observation.selected_after}"
            )
            print(
                "Focused bundle/role: "
                f"{observation.focus.bundle_id} / {observation.focus.role}"
            )
            print(f"Message: {observation.message}")
            print("Safety: bounded reads, exact input marker, one submission, no retry")
        return 1 if observation.verdict == "POST_DISPATCH_AMBIGUOUS" else 0
    except (ProbeError, subprocess.SubprocessError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
