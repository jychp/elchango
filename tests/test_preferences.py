from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from elchango.preferences import DEFAULT_ACTION_SLOTS, PreferencesStore


class PreferencesStoreTests(unittest.TestCase):
    def test_defaults_persist_atomically_and_reload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "preferences.json"
            store = PreferencesStore(path)

            self.assertEqual(store.snapshot().action_slots, DEFAULT_ACTION_SLOTS)
            store.set_session_icon("cursor:session-1", "robot")
            store.set_action_slot(0, "compact")

            reloaded = PreferencesStore(path).snapshot()
            self.assertEqual(reloaded.session_icons, {"cursor:session-1": "robot"})
            self.assertEqual(
                reloaded.action_slots,
                ("compact", "commit_push", "create_pr"),
            )
            self.assertEqual(tuple(path.parent.glob("*.tmp")), ())

    def test_invalid_schema_fails_explicitly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "preferences.json"
            path.write_text(
                json.dumps({"version": 99, "session_icons": {}, "action_slots": []}),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "unsupported"):
                PreferencesStore(path)


if __name__ == "__main__":
    unittest.main()
