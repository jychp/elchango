#!/usr/bin/env python3
"""POC 06: dispatch a personalized command to a Codex Desktop session.

Purpose
=======
Establish, without a live dispatch, whether elChango can safely deliver a
semantic command (``accept``, ``create_pr``, ``commit_push``, ``compact``) to an
exact Codex Desktop session. Every command is a privileged action: a focused
application is not enough; the exact session and the exact prompt-input target
must be proven first.

Method and evidence
===================
This POC follows the elChango new-command standard: dry-run by default,
``--execute`` gated, semantic-ID only, explicit recipe required when no official
mapping is proven, exact preflight, one-shot, no retry.

Findings for Codex Desktop:

- No static, authoritative selected-session signal exists (see POC 01/04). The
  shipped provider does not target a session at all: a command acts on whatever
  thread Codex has on screen, and elChango only verifies Codex is the frontmost
  app. There is no confirmation the keystrokes landed on the intended thread; the
  user is responsible for having it in front.
- ``accept`` has a naive keystroke recipe: a double Command+Return (no prompt
  text, no agent prompt-input target needed).
- ``create_pr``, ``commit_push``, ``compact`` are naive text recipes: type a
  fixed instruction into the focused composer, then submit with Command+Return.
  The Electron/Chromium app exposes no verified input-target marker, so this
  types into whatever the composer focus is (best-effort).

All four are wired best-effort in the shipped ``CodexProvider`` and the user
verifies the result. This POC stays a dry-run probe: it prints the plan and
never injects.

Safety and side effects
=======================
Read-only. ``--execute`` is accepted for interface parity but refuses to inject
because the required preflight evidence is absent. Nothing is typed or submitted.

Examples
========
    python scripts/poc/codex/06_codex_command_dispatch.py --command accept
    python scripts/poc/codex/06_codex_command_dispatch.py \
        --command compact --session-id <uuid> --recipe "/compact"

Interpretation
==============
``COMMANDS_WIRED_NAIVE``: all four commands are wired best-effort. They require
only that Codex is frontmost and act on the active window (a keystroke recipe for
``accept`` or a typed prompt for the text commands); the user verifies the
result. Reading the active thread from renderer state would let elChango confirm
the command hit the intended thread.

Official references
===================
https://developers.openai.com/codex/
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys


CODEX_BUNDLE_ID = "com.openai.codex"
SEMANTIC_IDS = ("accept", "create_pr", "commit_push", "compact")


def frontmost_bundle_id() -> str | None:
    try:
        completed = subprocess.run(
            [
                "/usr/bin/osascript",
                "-e",
                'tell application "System Events" to get bundle identifier '
                "of first application process whose frontmost is true",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return completed.stdout.strip() or None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only Codex Desktop command-dispatch probe.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--command",
        choices=SEMANTIC_IDS,
        required=True,
        help="Stable semantic command id (no arbitrary text is accepted).",
    )
    parser.add_argument("--session-id", default=None, help="Target native session id.")
    parser.add_argument(
        "--recipe",
        default=None,
        help="Explicit provider recipe (required in execute mode; unproven).",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Refuses to inject until preflight evidence exists.",
    )
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    frontmost = frontmost_bundle_id()

    # Exact preflight, in order. Each gate that cannot be satisfied statically
    # yields a conservative verdict and stops.
    preflight = {
        "semantic_id_known": args.command in SEMANTIC_IDS,
        "codex_frontmost": frontmost == CODEX_BUNDLE_ID,
        "target_session_selected": False,  # no static selected-session signal
        "prompt_input_verified": False,  # requires live AX evidence
        "recipe_supplied": bool(args.recipe),
        "recipe_proven_official": False,
    }

    if not preflight["target_session_selected"]:
        verdict = "TARGET_NOT_SELECTED"
    elif not preflight["prompt_input_verified"]:
        verdict = "INPUT_FOCUS_NOT_VERIFIED"
    else:
        verdict = "READY"

    injected = False
    if args.execute:
        # Fail closed: preflight evidence is absent, so never inject.
        injected = False
        verdict = "UNPROVEN_REQUIRES_LIVE_TARGET_EVIDENCE"

    result = {
        "command": args.command,
        "target_session_id": args.session_id,
        "preflight": preflight,
        "injected": injected,
        "verdict": verdict,
    }

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print("Codex Desktop command-dispatch probe")
        print(f"Command: {args.command}")
        print(f"Verdict: {verdict}")
        print("Preflight:")
        for key, value in preflight.items():
            print(f"  {key}: {value}")
        print("Required before any command can be enabled:")
        print("- an exact, verifiable selected-session signal;")
        print("- an accessibility target uniquely identifying the agent prompt;")
        print("- an official or explicitly configured, evidence-backed recipe.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
