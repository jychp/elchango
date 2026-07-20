#!/usr/bin/env python3
"""POC 04: attempt to focus one exact Codex Desktop session.

Purpose
=======
Determine whether elChango can focus an existing Codex Desktop session and,
critically, whether the exact target can be *verified* after the action. Focus
must never be enabled from a mechanism whose post-action target cannot be
confirmed.

Method and evidence
===================
Codex Desktop is ``com.openai.codex`` and registers the ``codex://`` URL scheme
(from its Info.plist). Two candidate focus mechanisms exist:

1. Activate the application (``open -b com.openai.codex`` / NSWorkspace). This
   only foregrounds the app; it does not select a specific thread.
2. A per-thread deep link. No such route is documented or observed. Recording
   absent evidence rather than inferring one, per the elChango new-harness
   standard.

Verification problem: POC 01 found NO static, authoritative "selected session"
signal for Codex Desktop (unlike Claude Desktop's unique ``lastFocusedAt`` or
Cursor's database). Without that signal, even a successful foreground cannot be
verified to have selected the intended thread.

This POC is read-only by default. It lists the target and prints the focus plan
and the verification gap. ``--execute`` would at most activate the application;
because exact post-action selection cannot be verified, the verdict stays
conservative. Per the issue #8 scope, no live experiment is run here.

Safety and side effects
=======================
Listing mode is read-only. ``--execute`` may foreground Codex Desktop. It never
submits a prompt, changes session content, or controls a running agent.

Examples
========
    python scripts/poc/codex/04_codex_desktop_focus.py
    python scripts/poc/codex/04_codex_desktop_focus.py --session-id <uuid>

Interpretation
==============
``FOCUS_NOT_VERIFIED_REQUIRES_LIVE``: no verifiable exact-session focus
mechanism has been established. elChango must keep the focus action rejected
(fail closed) until a live experiment proves an exact, verifiable mechanism.

Official references
===================
https://developers.openai.com/codex/
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


CODEX_BUNDLE_ID = "com.openai.codex"


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
    value = completed.stdout.strip()
    return value or None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only Codex Desktop focus probe.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Without --execute this command is read-only. Execute mode may only\n"
            "foreground Codex Desktop and cannot verify exact thread selection."
        ),
    )
    parser.add_argument("--session-id", default=None, help="Target native session id.")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Attempt to foreground Codex Desktop (cannot verify selection).",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.execute and not args.session_id:
        parser.error("--execute requires --session-id")
    return args


def main() -> int:
    args = parse_args()
    frontmost = frontmost_bundle_id()

    result = {
        "target_session_id": args.session_id,
        "codex_bundle_id": CODEX_BUNDLE_ID,
        "frontmost_bundle_id": frontmost,
        "codex_frontmost": frontmost == CODEX_BUNDLE_ID,
        "per_session_deep_link": None,
        "selected_session_signal": None,
        "executed": False,
        "verdict": "FOCUS_NOT_VERIFIED_REQUIRES_LIVE",
    }

    if args.execute:
        # Foregrounding only; exact selection remains unverifiable, so the
        # action is not accepted. Per issue #8 scope this branch is documented
        # but should not be run against a live app without explicit approval.
        result["executed"] = True
        result["verdict"] = "FOCUS_NOT_VERIFIED_REQUIRES_LIVE"

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print("Codex Desktop focus probe")
        print(f"Verdict: {result['verdict']}")
        print(f"Codex frontmost now: {result['codex_frontmost']}")
        print("Findings:")
        print("- No per-thread deep link is documented or observed.")
        print("- No static selected-session signal exists to verify a target.")
        print("- Application activation foregrounds the app but not a thread.")
        print("Conclusion: focus stays REJECTED (fail closed) pending a live,")
        print("verifiable exact-session mechanism.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
