from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from elchango.launch import (
    CursorLaunchController,
    _activate_cursor,
    _frontmost_application,
)
from elchango.providers.cursor import CursorProviderError


class CursorLaunchTests(unittest.TestCase):
    @patch("elchango.launch.time.sleep", return_value=None)
    @patch("elchango.launch.sys.platform", "darwin")
    def test_open_new_sends_one_sequence_and_keeps_prompt_manual(
        self,
        _sleep: object,
    ) -> None:
        calls: list[str] = []
        controller = CursorLaunchController(
            activate=lambda: calls.append("activate"),
            frontmost_application=lambda: "Cursor",
            send_shortcuts=lambda: calls.append("shortcuts"),
        )

        result = controller.open_new()

        self.assertEqual(calls, ["activate", "shortcuts"])
        self.assertEqual(result.verdict, "NEW_AGENT_VIEW_REQUESTED")
        self.assertTrue(result.executed)

    @patch("elchango.launch.time.sleep", return_value=None)
    @patch("elchango.launch.sys.platform", "darwin")
    def test_open_new_does_not_inject_when_cursor_is_not_foreground(
        self,
        _sleep: object,
    ) -> None:
        calls: list[str] = []
        controller = CursorLaunchController(
            activate=lambda: calls.append("activate"),
            frontmost_application=lambda: "Safari",
            send_shortcuts=lambda: calls.append("shortcuts"),
        )

        result = controller.open_new()

        self.assertEqual(calls, ["activate"])
        self.assertEqual(result.verdict, "CURSOR_NOT_FOREGROUND")
        self.assertFalse(result.executed)

    @patch(
        "elchango.launch.subprocess.run",
        side_effect=subprocess.TimeoutExpired("open", 10),
    )
    def test_activation_timeout_becomes_provider_error(self, _run: object) -> None:
        with self.assertRaisesRegex(CursorProviderError, "activation timed out"):
            _activate_cursor()

    @patch(
        "elchango.launch.subprocess.run",
        side_effect=subprocess.TimeoutExpired("osascript", 3),
    )
    def test_foreground_timeout_becomes_provider_error(self, _run: object) -> None:
        with self.assertRaisesRegex(CursorProviderError, "verification timed out"):
            _frontmost_application()


if __name__ == "__main__":
    unittest.main()
