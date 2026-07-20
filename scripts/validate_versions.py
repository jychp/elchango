#!/usr/bin/env python3
"""Validate the single elChango monorepo release version."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


def load_object(relative_path: str) -> dict[str, Any]:
    path = ROOT / relative_path
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{relative_path}: root must be an object")
    return value


def require_version(actual: object, expected: str, location: str) -> None:
    if actual != expected:
        raise ValueError(f"{location}: expected {expected}, found {actual!r}")


def root_package_version(document: dict[str, Any], location: str) -> object:
    packages = document.get("packages")
    if not isinstance(packages, dict):
        raise ValueError(f"{location}: packages must be an object")
    root_package = packages.get("")
    if not isinstance(root_package, dict):
        raise ValueError(f"{location}: root package is missing")
    return root_package.get("version")


def first_plugin_version(document: dict[str, Any], location: str) -> object:
    plugins = document.get("plugins")
    if not isinstance(plugins, list) or len(plugins) != 1:
        raise ValueError(f"{location}: exactly one plugin is required")
    plugin = plugins[0]
    if not isinstance(plugin, dict):
        raise ValueError(f"{location}: plugin must be an object")
    return plugin.get("version")


def validate(expected: str | None = None) -> str:
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    if SEMVER.fullmatch(version) is None:
        raise ValueError("VERSION must contain strict SemVer in X.Y.Z form")
    if expected is not None:
        require_version(version, expected, "VERSION")

    web_package = load_object("web/package.json")
    web_lock = load_object("web/package-lock.json")
    streamdeck_package = load_object("plugins/streamdeck/package.json")
    streamdeck_lock = load_object("plugins/streamdeck/package-lock.json")
    streamdeck_manifest = load_object(
        "plugins/streamdeck/com.jychp.elchango.sdPlugin/manifest.json"
    )
    cursor_manifest = load_object(
        "plugins/cursor/.cursor-plugin/plugin.json"
    )
    claude_manifest = load_object(
        "plugins/claude/.claude-plugin/plugin.json"
    )
    codex_manifest = load_object(
        "plugins/codex/.codex-plugin/plugin.json"
    )
    cursor_marketplace = load_object(".cursor-plugin/marketplace.json")
    claude_marketplace = load_object(".claude-plugin/marketplace.json")
    codex_marketplace = load_object(".agents/plugins/marketplace.json")

    checks = [
        (web_package.get("version"), "web/package.json version"),
        (web_lock.get("version"), "web/package-lock.json version"),
        (
            root_package_version(web_lock, "web/package-lock.json"),
            "web/package-lock.json root package version",
        ),
        (
            streamdeck_package.get("version"),
            "plugins/streamdeck/package.json version",
        ),
        (
            streamdeck_lock.get("version"),
            "plugins/streamdeck/package-lock.json version",
        ),
        (
            root_package_version(
                streamdeck_lock,
                "plugins/streamdeck/package-lock.json",
            ),
            "plugins/streamdeck/package-lock.json root package version",
        ),
        (
            cursor_manifest.get("version"),
            "plugins/cursor/.cursor-plugin/plugin.json version",
        ),
        (
            claude_manifest.get("version"),
            "plugins/claude/.claude-plugin/plugin.json version",
        ),
        (
            cursor_marketplace.get("metadata", {}).get("version")
            if isinstance(cursor_marketplace.get("metadata"), dict)
            else None,
            ".cursor-plugin/marketplace.json metadata.version",
        ),
        (
            first_plugin_version(
                cursor_marketplace,
                ".cursor-plugin/marketplace.json",
            ),
            ".cursor-plugin/marketplace.json plugin version",
        ),
        (
            claude_marketplace.get("version"),
            ".claude-plugin/marketplace.json version",
        ),
        (
            first_plugin_version(
                claude_marketplace,
                ".claude-plugin/marketplace.json",
            ),
            ".claude-plugin/marketplace.json plugin version",
        ),
        (
            codex_manifest.get("version"),
            "plugins/codex/.codex-plugin/plugin.json version",
        ),
        (
            codex_marketplace.get("version"),
            ".agents/plugins/marketplace.json version",
        ),
        (
            first_plugin_version(
                codex_marketplace,
                ".agents/plugins/marketplace.json",
            ),
            ".agents/plugins/marketplace.json plugin version",
        ),
    ]
    for actual, location in checks:
        require_version(actual, version, location)

    require_version(
        streamdeck_manifest.get("Version"),
        f"{version}.0",
        "plugins/streamdeck/com.jychp.elchango.sdPlugin/manifest.json Version",
    )
    return version


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate every elChango component against root VERSION."
    )
    parser.add_argument(
        "expected",
        nargs="?",
        help="Optional expected release version in X.Y.Z form.",
    )
    args = parser.parse_args()

    try:
        version = validate(args.expected)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        parser.exit(1, f"ERROR: {error}\n")

    print(f"Validated elChango monorepo version {version}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
