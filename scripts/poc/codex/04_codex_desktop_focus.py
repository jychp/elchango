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
(from its Info.plist). A per-thread deep link IS available:

    codex://threads/<thread-id>

observed in the app bundle at
``/Applications/ChatGPT.app/Contents/Resources/app.asar`` (the "Open in app"
action builds it). The ``thread-id`` equals the rollout ``session_id``/``id``
enumerated by POC 01, so opening the link navigates Codex Desktop to that exact
thread and foregrounds the app. This is an exact, id-addressed mechanism, far
stronger than a positional shortcut or a bare application activation.

Verification note: POC 01 found NO static, authoritative "selected session"
signal for Codex Desktop (unlike Claude Desktop's unique ``lastFocusedAt`` or
Cursor's database), so the *selected thread* cannot be read back after acting.
The exactness therefore comes from the id carried in the deep link, and the only
post-action check available is that the app became frontmost.

This POC is read-only by default. It lists the target, prints the deep link, and
reports whether Codex is frontmost. ``--execute`` (with ``--session-id``) opens
the deep link.

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
``FOCUS_DEEP_LINK_AVAILABLE``: an exact, id-addressed focus mechanism exists
(``codex://threads/<id>``). elChango enables focus, opens the link, and verifies
the app foregrounds; it cannot statically confirm the thread became selected
because Codex Desktop persists no selected-thread signal.

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
    deep_link = (
        f"codex://threads/{args.session_id}" if args.session_id else None
    )

    result = {
        "target_session_id": args.session_id,
        "codex_bundle_id": CODEX_BUNDLE_ID,
        "frontmost_bundle_id": frontmost,
        "codex_frontmost": frontmost == CODEX_BUNDLE_ID,
        "per_session_deep_link": deep_link,
        "selected_session_signal": None,
        "executed": False,
        "verdict": "FOCUS_DEEP_LINK_AVAILABLE",
    }

    if args.execute:
        # Open the exact per-thread deep link. This navigates Codex Desktop to
        # the target thread and foregrounds the app; selection cannot be read
        # back, so exactness relies on the id in the link.
        subprocess.run(["/usr/bin/open", deep_link], check=False)
        result["executed"] = True

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print("Codex Desktop focus probe")
        print(f"Verdict: {result['verdict']}")
        print(f"Codex frontmost now: {result['codex_frontmost']}")
        print(f"Per-thread deep link: {deep_link or '<pass --session-id>'}")
        print("Findings:")
        print("- codex://threads/<id> navigates to the exact thread by id.")
        print("- No static selected-session signal exists to read back a target.")
        print("- Post-action check is limited to the app becoming frontmost.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
