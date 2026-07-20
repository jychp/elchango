#!/usr/bin/env python3
"""POC 05: launch a neutral new Codex Desktop session.

Purpose
=======
Determine whether elChango can request a neutral new Codex Desktop session
without submitting a prompt or acting on any existing session.

Method and evidence
===================
Codex Desktop (``com.openai.codex``) registers the ``codex://`` URL scheme (from
its Info.plist ``CFBundleURLSchemes``). The app bundle's deep-link parser maps
the ``threads`` host as follows (read from
``/Applications/ChatGPT.app/Contents/Resources/app.asar``)::

    case `threads`:
        if segment[0] === `new` -> { kind: `newThread`, prompt?, originUrl?, path? }
        else                    -> { kind: `localConversation`, id }

So ``codex://threads/new`` opens a neutral new-thread surface. Input is attached
only through the optional ``prompt`` / ``originUrl`` / ``path`` query parameters;
with none present the link opens the composer and submits nothing. This is the
exact no-submit new-session route (the ``localConversation`` branch is the focus
route used by POC 04).

This POC prints the route by default. With ``--execute`` it opens
``codex://threads/new`` (foregrounding Codex Desktop's composer); it never adds a
``prompt`` parameter, so nothing is submitted.

Safety and side effects
=======================
Listing mode is read-only. ``--execute`` opens the neutral new-thread composer.
It never submits a prompt or acts on an existing session.

Examples
========
    python scripts/poc/codex/05_codex_new_session.py
    python scripts/poc/codex/05_codex_new_session.py --json

Interpretation
==============
``NEW_SESSION_DEEP_LINK_AVAILABLE``: an exact, no-submit new-session route exists
(``codex://threads/new``). elChango opens it and verifies the app foregrounds;
Codex persists no selected-thread signal, so the resulting thread is not read
back.

Official references
===================
https://developers.openai.com/codex/
https://learn.chatgpt.com/docs/hooks
"""

from __future__ import annotations

import argparse
import json
import plistlib
import subprocess
import sys
from pathlib import Path


CODEX_BUNDLE_ID = "com.openai.codex"
DEFAULT_APP_INFO_PLIST = Path("/Applications/ChatGPT.app/Contents/Info.plist")


def read_url_schemes(info_plist: Path) -> list[str]:
    if not info_plist.is_file():
        return []
    try:
        with info_plist.open("rb") as source:
            data = plistlib.load(source)
    except (OSError, plistlib.InvalidFileException):
        return []
    schemes: list[str] = []
    for entry in data.get("CFBundleURLTypes", []):
        for scheme in entry.get("CFBundleURLSchemes", []):
            if isinstance(scheme, str):
                schemes.append(scheme)
    return schemes


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only Codex Desktop new-session probe.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--info-plist",
        type=Path,
        default=DEFAULT_APP_INFO_PLIST,
        help=f"Codex Desktop Info.plist (default: {DEFAULT_APP_INFO_PLIST})",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Open the neutral codex://threads/new composer (submits nothing).",
    )
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


NEW_SESSION_DEEP_LINK = "codex://threads/new"


def main() -> int:
    args = parse_args()
    schemes = read_url_schemes(args.info_plist)
    scheme_present = "codex" in schemes

    executed = False
    if args.execute:
        subprocess.run(["/usr/bin/open", NEW_SESSION_DEEP_LINK], check=False)
        executed = True

    result = {
        "codex_bundle_id": CODEX_BUNDLE_ID,
        "url_schemes": schemes,
        "codex_scheme_present": scheme_present,
        "new_session_deep_link": NEW_SESSION_DEEP_LINK,
        "executed": executed,
        "verdict": "NEW_SESSION_DEEP_LINK_AVAILABLE",
    }

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print("Codex Desktop new-session probe")
        print(f"Verdict: {result['verdict']}")
        print(f"codex:// scheme present: {scheme_present}")
        print(f"Neutral new-thread deep link: {NEW_SESSION_DEEP_LINK}")
        print("Notes:")
        print("- The router maps threads/new -> newThread; no prompt param = no submit.")
        print("- Selection cannot be read back; foreground is the only post-check.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
