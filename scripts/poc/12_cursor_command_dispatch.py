#!/usr/bin/env python3
"""POC V4.1: conservatively dispatch a personalized command to Cursor.

Purpose
=======
Measure whether elChango can send one semantic command to one exact, already
selected Cursor agent without inventing a provider command mapping.

The only semantic IDs modeled here are ``accept``, ``create_pr``,
``commit_push``, and ``compact``. This POC does not claim that Cursor officially
maps any of them to a slash command, shortcut, or prompt. The operator must
supply the exact recipe with ``--recipe-text`` or ``--recipe-command``.

Method
======
The POC opens Cursor's SQLite database with ``mode=ro`` and
``PRAGMA query_only=ON``. It verifies:

1. the requested composer is a non-archived top-level session;
2. ``cursor/glass.selectedAgent`` exactly equals that composer ID;
3. Cursor is the frontmost application;
4. macOS Accessibility reports a focused, enabled text input whose metadata
   contains the operator-supplied ``--input-marker``;
5. all evidence is unchanged in an immediate second preflight.

Only then does execute mode type the supplied recipe. Text recipes press Return
once. Slash-command recipes wait for Cursor's suggestion UI, press Return to
select the command, wait again, and press Return to submit it. There is no
coordinate click, mapping fallback, or retry.

Safety and side effects
=======================
Default mode is read-only and dry-run. ``--execute`` is required to inject
text. Execute mode changes the target conversation by submitting exactly one
operator-supplied recipe. Accessibility permission is required. The marker is
necessary because application-level foreground status does not prove that the
agent prompt, rather than a terminal, search box, or editor, has input focus.

Examples
========
    python scripts/poc/12_cursor_command_dispatch.py
    python scripts/poc/12_cursor_command_dispatch.py accept --target <composer>
    python scripts/poc/12_cursor_command_dispatch.py compact \
      --target <composer> --recipe-command /known-command \
      --input-marker composer-input
    python scripts/poc/12_cursor_command_dispatch.py create_pr \
      --target <composer> --recipe-text "Create a pull request..." \
      --input-marker composer-input --execute

Interpretation
==============
``DRY_RUN`` means no input was sent. ``READY`` means all current preflight
evidence passed, but execution was not requested. ``DISPATCH_SENT`` means one
recipe was submitted and the target remained selected immediately afterward.
It does not prove that Cursor understood or completed the semantic command.
Any refusal verdict means no input was sent and must not be retried
automatically.

Limitations
===========
Cursor's database keys and accessibility metadata are undocumented. An input
marker must come from a focused observation on the installed Cursor version.
This probe cannot prove command completion or official command support.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


DEFAULT_DATABASE = (
    Path.home()
    / "Library/Application Support/Cursor/User/globalStorage/state.vscdb"
)
SELECTED_AGENT_KEY = "cursor/glass.selectedAgent"
SEMANTIC_IDS = ("accept", "create_pr", "commit_push", "compact")
TEXT_ROLES = {"AXTextArea", "AXTextField"}
CURSOR_BUNDLE_ID = "com.todesktop.230313mzl4w4u92"


class ProbeError(RuntimeError):
    """The local schema or targeting evidence is unsafe."""


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
    query_only: bool


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Probe exact-target Cursor command dispatch.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Dry-run is the default. No semantic ID has an official recipe in\n"
            "this POC. --execute sends one explicitly supplied recipe only."
        ),
    )
    parser.add_argument("semantic_id", nargs="?", choices=SEMANTIC_IDS)
    parser.add_argument("--target", help="Exact Cursor composer ID.")
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
        "--database",
        type=Path,
        default=DEFAULT_DATABASE,
        help=f"Cursor global state database (default: {DEFAULT_DATABASE}).",
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


def connect_read_only(database: Path) -> sqlite3.Connection:
    if not database.is_file():
        raise ProbeError(f"Cursor database not found: {database}")
    connection = sqlite3.connect(
        f"{database.resolve().as_uri()}?mode=ro",
        uri=True,
        timeout=2,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    tables = {
        row["name"]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    missing = {"ItemTable", "composerHeaders"} - tables
    if missing:
        connection.close()
        raise ProbeError(f"Unsupported Cursor schema; missing {sorted(missing)}")
    return connection


def decode_text(value: Any) -> str | None:
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return None
    return value if isinstance(value, str) else None


def selected_id(connection: sqlite3.Connection) -> str | None:
    row = connection.execute(
        "SELECT value FROM ItemTable WHERE key = ?",
        (SELECTED_AGENT_KEY,),
    ).fetchone()
    value = decode_text(row["value"]) if row else None
    return value.strip() if value and value.strip() else None


def validate_target(connection: sqlite3.Connection, target: str) -> None:
    row = connection.execute(
        """
        SELECT isArchived, isSubagent, value
        FROM composerHeaders
        WHERE composerId = ?
        """,
        (target,),
    ).fetchone()
    if row is None:
        raise ProbeError(f"Target composer not found: {target}")
    text = decode_text(row["value"])
    if not text:
        raise ProbeError("Target composer metadata is missing or undecodable.")
    try:
        candidate = json.loads(text)
    except json.JSONDecodeError as error:
        raise ProbeError("Target composer metadata is malformed JSON.") from error
    if not isinstance(candidate, dict):
        raise ProbeError("Target composer metadata must be a JSON object.")
    payload: dict[str, Any] = candidate
    if bool(row["isArchived"]):
        raise ProbeError("Target composer is archived.")
    if bool(row["isSubagent"]):
        raise ProbeError("Target composer is a subagent.")
    if bool(payload.get("isDraft")) or bool(payload.get("isEphemeral")):
        raise ProbeError("Target composer is draft or ephemeral.")


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
        and focus.bundle_id == CURSOR_BUNDLE_ID
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


def send_once(
    value: str,
    *,
    confirm_suggestion: bool,
    input_marker: str,
) -> None:
    script = r'''
on run argv
  my verifyInput(item 3 of argv, item 4 of argv, true)
  tell application "System Events"
    keystroke (item 1 of argv)
  end tell
  delay 0.5
  my verifyInput(item 3 of argv, item 4 of argv, false)
  tell application "System Events"
    key code 36
  end tell
  if (item 2 of argv) is "true" then
    delay 0.5
    my verifyInput(item 3 of argv, item 4 of argv, false)
    tell application "System Events"
      key code 36
    end tell
  end if
end run

on verifyInput(expectedBundle, expectedMarker, requireEmpty)
  tell application "System Events"
    set p to first application process whose frontmost is true
    if bundle identifier of p is not expectedBundle then error "frontmost bundle changed"
    set e to value of attribute "AXFocusedUIElement" of p
    set roleValue to value of attribute "AXRole" of e
    if roleValue is not "AXTextArea" and roleValue is not "AXTextField" then error "focused element is not a text input"
    if (value of attribute "AXEnabled" of e) is not true then error "focused input is disabled"
    set markerValue to (value of attribute "AXDOMClassList" of e) as text
    if markerValue is not expectedMarker then error "focused input marker changed"
    if requireEmpty and (value of attribute "AXNumberOfCharacters" of e) is not 0 then error "focused input is not empty"
  end tell
end verifyInput
'''
    completed = subprocess.run(
        [
            "/usr/bin/osascript",
            "-e",
            script,
            "--",
            value,
            "true" if confirm_suggestion else "false",
            CURSOR_BUNDLE_ID,
            input_marker,
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or "Input injection failed."
        raise ProbeError(detail)


def inspect(args: argparse.Namespace) -> DispatchResult:
    recipe_kind, recipe_value = recipe(args)
    with connect_read_only(args.database) as connection:
        query_only = bool(connection.execute("PRAGMA query_only").fetchone()[0])
        before = selected_id(connection)
        focus = accessibility_focus()
        if args.target:
            validate_target(connection, args.target)
        if not args.semantic_id or not args.target:
            return DispatchResult(
                "DRY_RUN",
                "Supply a semantic ID and exact target to evaluate dispatch.",
                args.semantic_id,
                args.target,
                recipe_kind,
                False,
                before,
                before,
                focus,
                query_only,
            )
        if before != args.target:
            return DispatchResult(
                "TARGET_NOT_SELECTED",
                "Exact target is not Cursor's selected agent; no input sent.",
                args.semantic_id,
                args.target,
                recipe_kind,
                False,
                before,
                before,
                focus,
                query_only,
            )
        if not focus_is_exact(focus, args.input_marker):
            return DispatchResult(
                "INPUT_FOCUS_NOT_VERIFIED",
                "Frontmost exact agent input evidence is insufficient; no input sent.",
                args.semantic_id,
                args.target,
                recipe_kind,
                False,
                before,
                before,
                focus,
                query_only,
            )
        if not recipe_value:
            return DispatchResult(
                "RECIPE_REQUIRED",
                "No official mapping is assumed; supply an explicit recipe.",
                args.semantic_id,
                args.target,
                recipe_kind,
                False,
                before,
                before,
                focus,
                query_only,
            )
        if not args.execute:
            return DispatchResult(
                "READY",
                "Current evidence passes; dry-run sent no input.",
                args.semantic_id,
                args.target,
                recipe_kind,
                False,
                before,
                before,
                focus,
                query_only,
            )

        latest_selected = selected_id(connection)
        latest_focus = accessibility_focus()
        if latest_selected != before or latest_focus != focus:
            return DispatchResult(
                "STALE_PREFLIGHT",
                "Target or focused input changed before injection; no input sent.",
                args.semantic_id,
                args.target,
                recipe_kind,
                False,
                before,
                latest_selected,
                latest_focus,
                query_only,
            )
        send_once(
            recipe_value,
            confirm_suggestion=recipe_kind == "command",
            input_marker=args.input_marker,
        )
        after = selected_id(connection)
        verdict = "DISPATCH_SENT" if after == args.target else "POST_DISPATCH_AMBIGUOUS"
        message = (
            "One recipe was submitted; semantic completion remains unverified."
            if verdict == "DISPATCH_SENT"
            else "One recipe was submitted, but exact target evidence changed."
        )
        return DispatchResult(
            verdict,
            message,
            args.semantic_id,
            args.target,
            recipe_kind,
            True,
            before,
            after,
            latest_focus,
            query_only,
        )


def main() -> int:
    args = parse_args()
    try:
        result = inspect(args)
        if args.json:
            print(json.dumps(asdict(result), indent=2, sort_keys=True))
        else:
            print("Cursor command-dispatch POC V4.1")
            print(f"VERDICT: {result.verdict}")
            print(f"Semantic ID: {result.semantic_id}")
            print(f"Target: {result.target}")
            print(f"Recipe kind: {result.recipe_kind}")
            print(f"Executed: {result.executed}")
            print(f"Selected before/after: {result.selected_before} / {result.selected_after}")
            print(f"Focused bundle/role: {result.focus.bundle_id} / {result.focus.role}")
            print(f"Message: {result.message}")
            print("Safety: read-only DB, exact input marker, one submission, no retry")
        return 1 if result.verdict in {"POST_DISPATCH_AMBIGUOUS"} else 0
    except (ProbeError, sqlite3.Error, subprocess.SubprocessError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
