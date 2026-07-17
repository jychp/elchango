#!/usr/bin/env python3
"""Build the bundled 15-key Stream Deck MK.2 profile deterministically."""

from __future__ import annotations

import json
import tempfile
import uuid
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "com.jychp.elchango.sdPlugin"
OUTPUT = PLUGIN / "elchango-mk2.streamDeckProfile"
PROFILE_ID = str(uuid.uuid5(uuid.NAMESPACE_DNS, "com.jychp.elchango.profile"))
DEFAULT_PAGE_ID = str(
    uuid.uuid5(uuid.NAMESPACE_DNS, "com.jychp.elchango.profile.default")
)
DECK_PAGE_ID = str(
    uuid.uuid5(uuid.NAMESPACE_DNS, "com.jychp.elchango.profile.deck")
)
ACTION_UUID = "com.jychp.elchango.key"


def main() -> None:
    """Write a profile containing the elChango action in every MK.2 slot."""

    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory) / f"{PROFILE_ID}.sdProfile"
        profiles = root / "Profiles"
        _write_json(
            root / "manifest.json",
            {
                "AppIdentifier": "*",
                "Device": {"Model": "20GBA9901", "UUID": ""},
                "Name": "elChango",
                "Pages": {
                    "Current": DECK_PAGE_ID,
                    "Default": DEFAULT_PAGE_ID,
                    "Pages": [DECK_PAGE_ID],
                },
                "Version": "3.0",
            },
        )
        _write_json(
            profiles / DEFAULT_PAGE_ID.upper() / "manifest.json",
            _page_manifest({}),
        )
        actions = {
            f"{column},{row}": _action(column, row)
            for row in range(3)
            for column in range(5)
        }
        _write_json(
            profiles / DECK_PAGE_ID.upper() / "manifest.json",
            _page_manifest(actions),
        )
        _write_archive(root)


def _action(column: int, row: int) -> dict[str, object]:
    action_id = str(
        uuid.uuid5(
            uuid.NAMESPACE_DNS,
            f"com.jychp.elchango.profile.action.{column}.{row}",
        )
    )
    return {
        "ActionID": action_id,
        "LinkedTitle": True,
        "Name": "Deck Key",
        "Plugin": {
            "Name": "elChango",
            "UUID": "com.jychp.elchango",
            "Version": "0.2.0.0",
        },
        "Resources": None,
        "Settings": {},
        "State": 0,
        "States": [
            {
                "FontFamily": "",
                "FontSize": 9,
                "FontStyle": "",
                "FontUnderline": False,
                "OutlineThickness": 2,
                "ShowTitle": False,
                "TitleAlignment": "middle",
                "TitleColor": "#ffffff",
            }
        ],
        "UUID": ACTION_UUID,
    }


def _page_manifest(actions: dict[str, object]) -> dict[str, object]:
    return {
        "Controllers": [{"Actions": actions, "Type": "Keypad"}],
        "Icon": "",
        "Name": "",
    }


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, separators=(",", ":"), sort_keys=True),
        encoding="utf-8",
    )


def _write_archive(root: Path) -> None:
    timestamp = (2026, 1, 1, 0, 0, 0)
    with zipfile.ZipFile(OUTPUT, "w", compression=zipfile.ZIP_STORED) as archive:
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(root.parent)
            info = zipfile.ZipInfo(str(relative), timestamp)
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = 0o644 << 16
            archive.writestr(info, path.read_bytes())


if __name__ == "__main__":
    main()
