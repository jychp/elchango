#!/usr/bin/env python3
"""POC 03: probe Codex Desktop lifecycle hooks for live state.

Purpose
=======
Establish, without running a live session, exactly how elChango would receive
authoritative lifecycle state from Codex Desktop, and what each event maps to.

Method and evidence
===================
Codex supports a plugin hook system with the same file schema as Claude Code
(official docs: https://learn.chatgpt.com/docs/hooks). Confirmed facts:

- A plugin declares hooks in ``.codex-plugin/plugin.json`` via
  ``"hooks": "./hooks/hooks.json"``.
- ``hooks.json`` maps event names to ``[{matcher?, hooks:[{type, command,
  commandWindows?, timeout?, statusMessage?}]}]``.
- Only ``type: "command"`` handlers run today; ``prompt`` and ``agent`` are
  parsed but skipped. There is no ``http`` hook type. elChango therefore relays
  through the existing ``elChangoHookReporter`` command (as the Cursor plugin
  does), not an HTTP hook (as the Claude Code plugin does).
- Supported events: thread/session scope ``SessionStart``, ``SubagentStart``;
  turn scope ``UserPromptSubmit``, ``PreToolUse``, ``PermissionRequest``,
  ``PostToolUse``, ``PreCompact``, ``PostCompact``, ``SubagentStop``, ``Stop``.
  ``SessionEnd`` is used by OpenAI's own example plugin.
- stdin payload fields: ``session_id``, ``transcript_path``, ``cwd``,
  ``hook_event_name``, ``model``, ``permission_mode``; turn events add
  ``turn_id``; tool events add ``tool_name``, ``tool_use_id``, ``tool_input``,
  and ``tool_response`` (PostToolUse only).
- Local corroboration: ``~/.codex/config.toml`` already records
  ``[hooks.state."...hooks/hooks.json:session_start:0:0"]`` for installed
  plugins, proving Codex Desktop honors this mechanism.

This POC generates the elChango Codex plugin files, prints the conservative
event -> provider-neutral state mapping, sanitizes a synthetic payload with a
strict key allow-list, and self-checks the generated schema. It performs NO
live run and installs nothing unless ``--out`` is given.

Safety
======
Read-only by default. ``--out DIR`` is the only side effect and writes only the
generated plugin scaffold under DIR. No live session, no network call.

Examples
========
    python scripts/poc/codex/03_codex_hook_probe.py
    python scripts/poc/codex/03_codex_hook_probe.py --emit-hooks
    python scripts/poc/codex/03_codex_hook_probe.py --json

Interpretation
==============
``UNPROVEN_REQUIRES_LIVE_HOOK_OBSERVATION``: the schema and mapping are grounded
in official docs and local config evidence, but no live Codex Desktop hook has
been observed reaching elChango, and the exact plugin install path for a local
(non-marketplace) plugin is not yet confirmed.

Official references
===================
https://learn.chatgpt.com/docs/hooks
https://developers.openai.com/codex/config-advanced
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPORTER = "/Applications/elChango.app/Contents/MacOS/elChangoHookReporter --provider codex"

# Conservative event -> (state, note). Waiting is orange; working blue; done
# green; idle gray. Only states elChango can defend from the event alone.
EVENT_STATE = {
    "SessionStart": ("idle", "session opened or resumed"),
    "UserPromptSubmit": ("working", "user submitted a turn"),
    "PreToolUse": ("working", "tool about to run"),
    "PermissionRequest": ("waiting", "awaiting user approval"),
    "PostToolUse": ("working", "tool finished, turn continues"),
    "PreCompact": ("working", "compaction started"),
    "PostCompact": ("working", "compaction finished, turn continues"),
    "SubagentStart": ("working", "subagent progress"),
    "SubagentStop": ("working", "subagent finished, parent may continue"),
    "Stop": ("done", "turn finished"),
    "SessionEnd": ("idle", "session ended"),
}

# Events elChango subscribes to and the matcher to apply (None means all).
SUBSCRIBED = {
    "SessionStart": None,
    "UserPromptSubmit": None,
    "PreToolUse": None,
    "PermissionRequest": None,
    "PostToolUse": None,
    "PreCompact": None,
    "PostCompact": None,
    "SubagentStart": None,
    "SubagentStop": None,
    "Stop": None,
    "SessionEnd": None,
}

# Strict allow-list of payload keys elChango retains. No prompt, assistant,
# tool input/output, or transcript content is kept.
ALLOWED_PAYLOAD_KEYS = {
    "hook_event_name",
    "session_id",
    "cwd",
    "transcript_path",
    "tool_name",
    "permission_mode",
    "turn_id",
}

PLUGIN_MANIFEST = {
    "name": "elchango-codex",
    "description": "Reports Codex Desktop lifecycle events to the local elChango app.",
    "hooks": "./hooks/hooks.json",
}


def build_hooks_json() -> dict:
    hooks: dict[str, list] = {}
    for event, matcher in SUBSCRIBED.items():
        entry: dict = {
            "hooks": [
                {
                    "type": "command",
                    "command": REPORTER,
                    "timeout": 2,
                }
            ]
        }
        if matcher is not None:
            entry["matcher"] = matcher
        hooks[event] = [entry]
    return {
        "description": "Report Codex Desktop lifecycle events to elChango.",
        "hooks": hooks,
    }


def sanitize(payload: dict) -> dict:
    return {key: payload[key] for key in ALLOWED_PAYLOAD_KEYS if key in payload}


def self_check(hooks_json: dict) -> list[str]:
    problems: list[str] = []
    supported = set(EVENT_STATE)
    for event, entries in hooks_json["hooks"].items():
        if event not in supported:
            problems.append(f"event {event} is not in the supported set")
        for entry in entries:
            for hook in entry.get("hooks", []):
                if hook.get("type") != "command":
                    problems.append(f"{event}: only command hooks run in Codex")
                if "elChangoHookReporter" not in hook.get("command", ""):
                    problems.append(f"{event}: command must relay to the reporter")
    return problems


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only Codex Desktop hook probe and mapping check.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--emit-hooks",
        action="store_true",
        help="Print the generated hooks.json and plugin manifest.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Write the plugin scaffold under this directory (only side effect).",
    )
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    hooks_json = build_hooks_json()
    manifest = PLUGIN_MANIFEST
    problems = self_check(hooks_json)

    synthetic = {
        "hook_event_name": "PermissionRequest",
        "session_id": "019f7d26-71cf-75f1-a1fd-85ae188a2820",
        "cwd": "/Users/example/project",
        "transcript_path": "/Users/example/.codex/sessions/.../rollout.jsonl",
        "tool_name": "shell",
        "tool_input": {"command": "rm -rf secret"},  # dropped by sanitize
        "permission_mode": "on-request",
        "model": "gpt-5.6",  # dropped by sanitize
    }
    sanitized = sanitize(synthetic)
    dropped = sorted(set(synthetic) - set(sanitized))

    verdict = (
        "UNPROVEN_REQUIRES_LIVE_HOOK_OBSERVATION"
        if not problems
        else "HOOK_SCHEMA_SELF_CHECK_FAILED"
    )

    if args.out is not None:
        plugin_dir = args.out / "elchango-codex"
        (plugin_dir / ".codex-plugin").mkdir(parents=True, exist_ok=True)
        (plugin_dir / "hooks").mkdir(parents=True, exist_ok=True)
        (plugin_dir / ".codex-plugin" / "plugin.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        (plugin_dir / "hooks" / "hooks.json").write_text(
            json.dumps(hooks_json, indent=2) + "\n", encoding="utf-8"
        )

    if args.json:
        print(
            json.dumps(
                {
                    "verdict": verdict,
                    "self_check_problems": problems,
                    "event_state_mapping": {k: v[0] for k, v in EVENT_STATE.items()},
                    "sanitized_payload": sanitized,
                    "dropped_keys": dropped,
                    "manifest": manifest,
                    "hooks_json": hooks_json if args.emit_hooks else "<use --emit-hooks>",
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print("Codex Desktop hook probe")
        print(f"Verdict: {verdict}")
        print("Relay: command hook -> elChangoHookReporter --provider codex")
        print("Event -> state mapping:")
        for event, (state, note) in EVENT_STATE.items():
            print(f"  {event}: {state} ({note})")
        print(f"Sanitized payload keys: {sorted(sanitized)}")
        print(f"Dropped payload keys: {dropped}")
        if problems:
            print("Self-check problems:")
            for problem in problems:
                print(f"- {problem}")
        if args.emit_hooks:
            print("plugin.json:")
            print(json.dumps(manifest, indent=2))
            print("hooks.json:")
            print(json.dumps(hooks_json, indent=2))
        print("Limitations:")
        print("- No live Codex Desktop hook has been observed reaching elChango.")
        print("- The install path for a local (non-marketplace) plugin is unconfirmed.")
        print("- Correlation of hook session_id with rollout session_id needs live proof.")
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
