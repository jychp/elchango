"""Command-line entry point for the local elChango service."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from elchango.activity import ActivityStore
from elchango.claude_activity import ClaudeActivityStore
from elchango.deck import DeckService
from elchango.focus import CursorFocusController
from elchango.hook_reporter import DEFAULT_HOOK_ENDPOINT, report_hook
from elchango.launch import CursorLaunchController
from elchango.providers.cursor import (
    DEFAULT_DATABASE,
    DEFAULT_WORKSPACE_STORAGE,
    CursorProvider,
    CursorProviderError,
)
from elchango.providers.cursor_adapter import CursorAdapter
from elchango.providers.claude_code import (
    DEFAULT_CLAUDE_PROJECTS,
    DEFAULT_DESKTOP_SESSIONS_ROOT,
    ClaudeCodeProvider,
    ClaudeCodeProviderError,
)
from elchango.server import serve


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

    activity_store = ActivityStore()
    provider = CursorProvider(
        database=args.database,
        workspace_storage=args.workspace_storage,
        activity_store=activity_store,
    )
    try:
        provider.snapshot()
    except CursorProviderError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2

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
    claude_activity_store = ClaudeActivityStore()
    claude = ClaudeCodeProvider(
        desktop_sessions_root=args.claude_desktop_sessions,
        projects_root=args.claude_projects,
        activity_store=claude_activity_store,
    )
    try:
        claude.snapshot()
    except ClaudeCodeProviderError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    providers = {
        cursor.provider_id: cursor,
        claude.provider_id: claude,
    }
    service = DeckService(providers, default_provider_id=cursor.provider_id)
    url = f"http://{args.host}:{args.port}/"
    print("elChango v0.2")
    print(f"Deck: {url}")
    print(f"Cursor database: {args.database}")
    print(f"Claude Desktop sessions: {args.claude_desktop_sessions}")
    print("Session focus: enabled with exact post-action verification")
    print("New Agent view: enabled; prompt submission remains manual")
    print("Agent actions: disabled")
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
            hook_recorders={
                cursor.provider_id: activity_store.record,
                claude.provider_id: claude_activity_store.record,
            },
        )
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0
