#!/usr/bin/env python3
"""POC 05: launch a neutral new Codex Desktop session.

Purpose
=======
Determine whether elChango can request a neutral new Codex Desktop session
without submitting a prompt or acting on any existing session.

Method and evidence
===================
Codex Desktop (``com.openai.codex``) registers the ``codex://`` URL scheme (from
its Info.plist ``CFBundleURLSchemes``). Candidate neutral-launch mechanisms:

1. ``open -b com.openai.codex`` / ``open -a ChatGPT`` - foregrounds or launches
   the app; whether it presents a new-session surface is unconfirmed.
2. A ``codex://`` new-session deep link. The exact path (for example
   ``codex://new``) is NOT documented or observed. Recording absent evidence
   rather than inferring a route.

This POC is read-only by default and prints the candidate mechanisms. With
``--execute`` it would open the app's neutral surface; per issue #8 scope no
live experiment is run here, and any accepted verdict requires live proof that
the resulting surface is a neutral new-session screen that submits nothing.

Safety and side effects
=======================
Listing mode is read-only. ``--execute`` may launch or foreground Codex Desktop.
It never submits a prompt or acts on an existing session.

Examples
========
    python scripts/poc/codex/05_codex_new_session.py
    python scripts/poc/codex/05_codex_new_session.py --json

Interpretation
==============
``NEW_SESSION_CANDIDATE_UNVERIFIED``: a launch mechanism is plausible but the
exact neutral new-session route and its no-submit guarantee are unproven.

Official references
===================
https://developers.openai.com/codex/
"""

from __future__ import annotations

import argparse
import json
import plistlib
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
        help="Attempt to open a neutral new-session surface (not run for issue #8).",
    )
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    schemes = read_url_schemes(args.info_plist)
    scheme_present = "codex" in schemes

    result = {
        "codex_bundle_id": CODEX_BUNDLE_ID,
        "url_schemes": schemes,
        "codex_scheme_present": scheme_present,
        "candidate_deep_link": "codex://new (unconfirmed)",
        "candidate_app_launch": f"open -b {CODEX_BUNDLE_ID}",
        "executed": False,
        "verdict": "NEW_SESSION_CANDIDATE_UNVERIFIED",
    }

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print("Codex Desktop new-session probe")
        print(f"Verdict: {result['verdict']}")
        print(f"Registered URL schemes: {schemes or '<none>'}")
        print(f"codex:// scheme present: {scheme_present}")
        print("Candidates:")
        print("- App launch: open -b com.openai.codex (surface unconfirmed)")
        print("- Deep link: codex://new (route unconfirmed, not observed)")
        print("Limitations:")
        print("- Exact neutral new-session route is undocumented and unobserved.")
        print("- No-submit guarantee requires a live experiment to confirm.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
