"""Command-line entry point for the local elChango service."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from elchango.activity import ActivityStore
from elchango.claude_activity import ClaudeActivityStore
from elchango.deck import DeckService
from elchango.focus import CursorFocusController
from elchango.hook_reporter import DEFAULT_HOOK_ENDPOINT, report_hook
from elchango.launch import CursorLaunchController
from elchango.preferences import DEFAULT_PREFERENCES_PATH, PreferencesStore
from elchango.providers.cursor import (
    DEFAULT_DATABASE,
    DEFAULT_WORKSPACE_STORAGE,
    CursorProvider,
    CursorProviderError,
)
from elchango.providers.cursor_adapter import CursorAdapter
from elchango.providers.claude_code import (
    DEFAULT_CLAUDE_DESKTOP_CONFIG,
    DEFAULT_CLAUDE_PROJECTS,
    DEFAULT_DESKTOP_SESSIONS_ROOT,
    ClaudeCodeProvider,
    ClaudeCodeProviderError,
)
from elchango.server import serve

CURSOR_BUNDLE_ID = "com.todesktop.230313mzl4w4u92"
CLAUDE_BUNDLE_ID = "com.anthropic.claudefordesktop"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="chango",
        description="Run the local elChango agent deck.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    serve_parser = subparsers.add_parser(
        "serve",
        help="Serve the real Cursor-backed web deck.",
    )
    serve_parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Loopback bind address (default: 127.0.0.1).",
    )
    serve_parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="HTTP port (default: 8765).",
    )
    serve_parser.add_argument(
        "--assets",
        type=Path,
        default=Path("web/dist"),
        help="Compiled web asset directory (default: web/dist).",
    )
    serve_parser.add_argument(
        "--api-only",
        action="store_true",
        help="Serve APIs without requiring or serving compiled web assets.",
    )
    serve_parser.add_argument(
        "--database",
        type=Path,
        default=DEFAULT_DATABASE,
        help=f"Cursor state database (default: {DEFAULT_DATABASE}).",
    )
    serve_parser.add_argument(
        "--workspace-storage",
        type=Path,
        default=DEFAULT_WORKSPACE_STORAGE,
        help=f"Cursor workspace metadata (default: {DEFAULT_WORKSPACE_STORAGE}).",
    )
    serve_parser.add_argument(
        "--claude-desktop-sessions",
        type=Path,
        default=DEFAULT_DESKTOP_SESSIONS_ROOT,
        help=(
            "Claude Desktop persistent Code sessions "
            f"(default: {DEFAULT_DESKTOP_SESSIONS_ROOT})."
        ),
    )
    serve_parser.add_argument(
        "--claude-projects",
        type=Path,
        default=DEFAULT_CLAUDE_PROJECTS,
        help=f"Claude Code transcript root (default: {DEFAULT_CLAUDE_PROJECTS}).",
    )
    serve_parser.add_argument(
        "--claude-desktop-config",
        type=Path,
        default=DEFAULT_CLAUDE_DESKTOP_CONFIG,
        help=f"Claude Desktop config (default: {DEFAULT_CLAUDE_DESKTOP_CONFIG}).",
    )
    serve_parser.add_argument(
        "--preferences",
        type=Path,
        default=DEFAULT_PREFERENCES_PATH,
        help=f"Deck preferences (default: {DEFAULT_PREFERENCES_PATH}).",
    )
    hook_parser = subparsers.add_parser(
        "report-hook",
        help="Forward one Cursor lifecycle hook to a running deck.",
    )
    hook_parser.add_argument(
        "--endpoint",
        default=os.environ.get("ELCHANGO_HOOK_ENDPOINT", DEFAULT_HOOK_ENDPOINT),
        help="Loopback Cursor hook endpoint.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "report-hook":
        report_hook(sys.stdin, sys.stdout, args.endpoint)
        return 0
    if args.command != "serve":
        parser.error(f"unsupported command: {args.command}")
    if args.host not in {"127.0.0.1", "::1", "localhost"}:
        parser.error("--host must be a loopback address")
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")

    assets = args.assets.resolve()
    if not args.api_only and not (assets / "index.html").is_file():
        print(
            f"ERROR: compiled web assets not found at {assets}. "
            "Run `npm --prefix web run build` first.",
            file=sys.stderr,
        )
        return 2

    providers = {}
    unavailable_providers: dict[str, str] = {}
    hook_recorders = {}
    activity_store = ActivityStore()
    if _application_bundle_available("Cursor", CURSOR_BUNDLE_ID):
        provider = CursorProvider(
            database=args.database,
            workspace_storage=args.workspace_storage,
            activity_store=activity_store,
        )
        try:
            provider.snapshot()
        except CursorProviderError as error:
            unavailable_providers["cursor"] = str(error)
        else:
            focus_controller = CursorFocusController(
                database=args.database,
                workspace_storage=args.workspace_storage,
            )
            launch_controller = CursorLaunchController()
            cursor = CursorAdapter(
                inventory=provider,
                focus_controller=focus_controller,
                launch_controller=launch_controller,
                activity_store=activity_store,
            )
            providers[cursor.provider_id] = cursor
            hook_recorders[cursor.provider_id] = activity_store.record
    else:
        unavailable_providers["cursor"] = "Cursor application is not installed"

    claude_activity_store = ClaudeActivityStore()
    if _application_bundle_available("Claude", CLAUDE_BUNDLE_ID):
        claude = ClaudeCodeProvider(
            desktop_sessions_root=args.claude_desktop_sessions,
            projects_root=args.claude_projects,
            desktop_config=args.claude_desktop_config,
            activity_store=claude_activity_store,
        )
        try:
            claude.snapshot()
        except ClaudeCodeProviderError as error:
            unavailable_providers["claude-code"] = str(error)
        else:
            providers[claude.provider_id] = claude
            hook_recorders[claude.provider_id] = claude.record_hook
    else:
        unavailable_providers["claude-code"] = (
            "Claude Desktop application is not installed"
        )

    try:
        preferences = PreferencesStore(args.preferences)
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    service = DeckService(providers, preferences=preferences)
    url = f"http://{args.host}:{args.port}/"
    print("elChango v0.2")
    print(f"Deck: {url}")
    print(f"Cursor database: {args.database}")
    print(f"Claude Desktop sessions: {args.claude_desktop_sessions}")
    for provider_id, reason in unavailable_providers.items():
        print(f"Provider unavailable: {provider_id}: {reason}")
    focus_providers = sorted(
        provider_id
        for provider_id, provider in providers.items()
        if "focus_session" in provider.capabilities
    )
    launch_providers = sorted(
        provider_id
        for provider_id, provider in providers.items()
        if "new_session" in provider.capabilities
    )
    print(
        "Session focus: "
        + (
            f"enabled for {', '.join(focus_providers)} with exact verification"
            if focus_providers
            else "disabled; no available provider supports focus"
        )
    )
    print(
        "New Agent view: "
        + (
            f"enabled for {', '.join(launch_providers)}; submission remains manual"
            if launch_providers
            else "disabled; no available provider supports launch"
        )
    )
    command_providers = sorted(
        provider_id
        for provider_id, provider in providers.items()
        if "execute_command" in provider.capabilities
    )
    print(
        "Agent actions: "
        + (
            f"enabled for {', '.join(command_providers)} with target verification"
            if command_providers
            else "disabled; no available provider supports commands"
        )
    )
    print("Press Ctrl-C to stop.")
    try:
        serve(
            service,
            activity_store,
            providers,
            assets,
            args.host,
            args.port,
            args.api_only,
            hook_recorders=hook_recorders,
            unavailable_providers=unavailable_providers,
        )
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


def _application_bundle_available(
    application_name: str,
    expected_bundle_id: str,
) -> bool:
    """Return whether Launch Services resolves the expected macOS application."""

    if sys.platform != "darwin":
        return False
    try:
        result = subprocess.run(
            [
                "/usr/bin/osascript",
                "-e",
                f'id of application "{application_name}"',
            ],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and result.stdout.strip() == expected_bundle_id
