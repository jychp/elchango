#!/usr/bin/env python3
"""Validate elChango provider plugin manifests and hook safety contracts."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
VERSION = "1.0.0"
SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
REPORTER_COMMAND = (
    "/Applications/elChango.app/Contents/MacOS/"
    "elChangoHookReporter --provider cursor"
)
HOOK_URL = "http://127.0.0.1:8765/api/hooks/claude-code"


def load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"{path.relative_to(ROOT)}: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"{path.relative_to(ROOT)}: root must be an object")
    return value


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def require_allowed_keys(
    value: dict[str, Any],
    allowed: set[str],
    path: Path,
) -> None:
    unexpected = sorted(set(value) - allowed)
    require(not unexpected, f"{path}: unsupported keys: {unexpected}")


def validate_marketplace(provider: str, hidden_directory: str) -> None:
    path = ROOT / hidden_directory / "marketplace.json"
    marketplace = load_object(path)
    allowed = {"name", "owner", "plugins"}
    if provider == "cursor":
        allowed.add("metadata")
    else:
        allowed.update({"$schema", "description", "version"})
    require_allowed_keys(marketplace, allowed, path)
    require(marketplace.get("name") == "elchango", f"{path}: invalid name")
    owner = marketplace.get("owner")
    require(
        isinstance(owner, dict) and isinstance(owner.get("name"), str),
        f"{path}: owner.name is required",
    )
    plugins = marketplace.get("plugins")
    require(
        isinstance(plugins, list) and len(plugins) == 1,
        f"{path}: exactly one plugin is required",
    )
    plugin = plugins[0]
    require(isinstance(plugin, dict), f"{path}: plugin must be an object")
    require_allowed_keys(
        plugin,
        {"name", "source", "description", "version"},
        path,
    )
    require(plugin.get("name") == "elchango", f"{path}: invalid plugin name")
    require(
        isinstance(plugin.get("description"), str),
        f"{path}: plugin description is required",
    )
    require(
        plugin.get("source") == f"./plugins/{provider}",
        f"{path}: invalid plugin source",
    )
    declared_versions = [
        marketplace.get("version"),
        marketplace.get("metadata", {}).get("version")
        if isinstance(marketplace.get("metadata"), dict)
        else None,
        plugin.get("version"),
    ]
    require(
        all(value in {None, VERSION} for value in declared_versions),
        f"{path}: duplicated versions must match {VERSION}",
    )


def validate_manifest(provider: str, hidden_directory: str) -> None:
    path = ROOT / "plugins" / provider / hidden_directory / "plugin.json"
    manifest = load_object(path)
    allowed = {
        "name",
        "displayName",
        "version",
        "description",
        "author",
        "homepage",
        "repository",
        "keywords",
        "hooks",
    }
    if provider == "claude":
        allowed.add("$schema")
    require_allowed_keys(manifest, allowed, path)
    require(manifest.get("name") == "elchango", f"{path}: invalid name")
    require(
        isinstance(manifest.get("description"), str),
        f"{path}: description is required",
    )
    require(
        isinstance(manifest.get("author"), dict)
        and isinstance(manifest["author"].get("name"), str),
        f"{path}: author.name is required",
    )
    version = manifest.get("version")
    require(
        isinstance(version, str) and SEMVER.fullmatch(version) is not None,
        f"{path}: version must be SemVer",
    )
    require(version == VERSION, f"{path}: expected version {VERSION}")
    hooks = manifest.get("hooks")
    require(isinstance(hooks, str), f"{path}: hooks path is required")
    hook_path = (path.parents[1] / hooks).resolve()
    require(
        hook_path.is_relative_to(path.parents[1].resolve()),
        f"{path}: hooks path escapes the plugin",
    )
    require(hook_path.is_file(), f"{path}: hooks file does not exist")


def validate_cursor() -> None:
    validate_marketplace("cursor", ".cursor-plugin")
    validate_manifest("cursor", ".cursor-plugin")
    path = ROOT / "plugins/cursor/hooks/hooks.json"
    document = load_object(path)
    version = document.get("version")
    require(
        type(version) is int and version == 1,
        f"{path}: schema version must be integer 1",
    )
    hooks = document.get("hooks")
    require(isinstance(hooks, dict), f"{path}: hooks must be an object")
    expected = {
        "sessionStart",
        "beforeSubmitPrompt",
        "stop",
        "sessionEnd",
    }
    require(set(hooks) == expected, f"{path}: unexpected Cursor hook events")
    for event, handlers in hooks.items():
        require(
            isinstance(handlers, list) and len(handlers) == 1,
            f"{path}: {event} must have exactly one handler",
        )
        require(
            handlers[0]
            == {
                "command": REPORTER_COMMAND,
                "timeout": 1,
                "failClosed": False,
            },
            f"{path}: {event} must preserve the fail-open reporter contract",
        )


def validate_claude() -> None:
    validate_marketplace("claude", ".claude-plugin")
    validate_manifest("claude", ".claude-plugin")
    path = ROOT / "plugins/claude/hooks/hooks.json"
    document = load_object(path)
    hooks = document.get("hooks")
    require(isinstance(hooks, dict), f"{path}: hooks must be an object")
    expected_matchers: dict[str, str | None] = {
        "SessionStart": None,
        "UserPromptSubmit": None,
        "PreToolUse": "AskUserQuestion|ExitPlanMode",
        "PostToolUse": "AskUserQuestion|ExitPlanMode",
        "PermissionRequest": None,
        "Elicitation": None,
        "ElicitationResult": None,
        "Notification": (
            "permission_prompt|idle_prompt|elicitation_dialog|agent_needs_input"
        ),
        "Stop": None,
        "StopFailure": None,
        "SessionEnd": None,
    }
    require(set(hooks) == set(expected_matchers), f"{path}: unexpected events")
    expected_http = {"type": "http", "url": HOOK_URL, "timeout": 2}
    for event, matcher in expected_matchers.items():
        groups = hooks[event]
        require(
            isinstance(groups, list) and len(groups) == 1,
            f"{path}: {event} must have exactly one hook group",
        )
        group = groups[0]
        require(isinstance(group, dict), f"{path}: {event} group is invalid")
        require_allowed_keys(
            group,
            {"hooks"} if matcher is None else {"matcher", "hooks"},
            path,
        )
        if matcher is None:
            require("matcher" not in group, f"{path}: {event} matcher is invalid")
        else:
            require(
                group.get("matcher") == matcher,
                f"{path}: {event} matcher is invalid",
            )
        require(
            group.get("hooks") == [expected_http],
            f"{path}: {event} must use the local HTTP hook contract",
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate elChango Cursor and Claude provider plugins."
    )
    parser.add_argument("provider", choices=("cursor", "claude", "all"))
    args = parser.parse_args()

    try:
        if args.provider in {"cursor", "all"}:
            validate_cursor()
        if args.provider in {"claude", "all"}:
            validate_claude()
    except ValueError as error:
        parser.exit(1, f"ERROR: {error}\n")

    print(f"Validated {args.provider} provider plugin contract.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
