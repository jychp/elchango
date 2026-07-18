#!/usr/bin/env python3
"""Validate synchronized Stream Deck package and manifest versions."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "plugins/streamdeck"
SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


def load(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: root must be an object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate Stream Deck package version consistency."
    )
    parser.add_argument(
        "expected",
        nargs="?",
        help="Optional release version in X.Y.Z form.",
    )
    args = parser.parse_args()

    try:
        package = load(PACKAGE_ROOT / "package.json")
        lock = load(PACKAGE_ROOT / "package-lock.json")
        manifest = load(
            PACKAGE_ROOT / "com.jychp.elchango.sdPlugin/manifest.json"
        )
        package_version = package.get("version")
        lock_version = lock.get("version")
        lock_packages = lock.get("packages")
        manifest_version = manifest.get("Version")
        if not isinstance(package_version, str) or not SEMVER.fullmatch(
            package_version
        ):
            raise ValueError("package.json version must use X.Y.Z")
        if lock_version != package_version:
            raise ValueError("package-lock.json version does not match package.json")
        if (
            not isinstance(lock_packages, dict)
            or not isinstance(lock_packages.get(""), dict)
            or lock_packages[""].get("version") != package_version
        ):
            raise ValueError(
                "package-lock.json root package version does not match package.json"
            )
        if manifest_version != f"{package_version}.0":
            raise ValueError(
                "Stream Deck manifest version must equal package version plus .0"
            )
        if args.expected is not None and args.expected != package_version:
            raise ValueError(
                f"release {args.expected} does not match plugin {package_version}"
            )
    except (OSError, ValueError, json.JSONDecodeError) as error:
        parser.exit(1, f"ERROR: {error}\n")

    print(f"Validated Stream Deck version {package_version}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
