#!/usr/bin/env python3
"""Create deterministic ZIP archives for local provider plugin testing."""

from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXED_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
PLUGIN_FILES = {
    "cursor": {
        ".cursor-plugin/plugin.json",
        "CHANGELOG.md",
        "README.md",
        "hooks/hooks.json",
    },
    "claude": {
        ".claude-plugin/plugin.json",
        "CHANGELOG.md",
        "README.md",
        "hooks/hooks.json",
    },
}
REPOSITORY_NOTICES = ("LICENSE", "TRADEMARKS.md")


def plugin_version(provider: str) -> str:
    hidden_directory = ".cursor-plugin" if provider == "cursor" else ".claude-plugin"
    manifest_path = (
        ROOT / "plugins" / provider / hidden_directory / "plugin.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    version = manifest.get("version")
    if not isinstance(version, str):
        raise ValueError(f"{manifest_path}: version must be a string")
    return version


def package(provider: str, output_directory: Path) -> Path:
    plugin_root = ROOT / "plugins" / provider
    output_directory.mkdir(parents=True, exist_ok=True)
    destination = (
        output_directory / f"elchango-{provider}-{plugin_version(provider)}.zip"
    )
    for path in plugin_root.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"{path}: plugin packages cannot contain symlinks")
    actual_files = {
        path.relative_to(plugin_root).as_posix()
        for path in plugin_root.rglob("*")
        if path.is_file()
    }
    expected_files = PLUGIN_FILES[provider]
    if actual_files != expected_files:
        unexpected = sorted(actual_files - expected_files)
        missing = sorted(expected_files - actual_files)
        raise ValueError(
            f"{plugin_root}: unexpected files={unexpected}, missing files={missing}"
        )
    files = [plugin_root / relative for relative in sorted(expected_files)]

    with zipfile.ZipFile(
        destination,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for path in files:
            relative_path = path.relative_to(plugin_root).as_posix()
            info = zipfile.ZipInfo(relative_path, FIXED_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes(), compresslevel=9)
        for relative_path in REPOSITORY_NOTICES:
            info = zipfile.ZipInfo(relative_path, FIXED_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(
                info,
                (ROOT / relative_path).read_bytes(),
                compresslevel=9,
            )
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Package one elChango provider plugin as a deterministic ZIP."
    )
    parser.add_argument("provider", choices=("cursor", "claude"))
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "dist",
        help="Archive output directory (default: repository dist/).",
    )
    args = parser.parse_args()

    output_directory = args.output_dir
    if not output_directory.is_absolute():
        output_directory = ROOT / output_directory
    try:
        destination = package(args.provider, output_directory)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        parser.exit(1, f"ERROR: {error}\n")
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
