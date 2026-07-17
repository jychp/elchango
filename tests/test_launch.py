from __future__ import annotations

import unittest
from unittest.mock import patch

from elchango.launch import CursorLaunchController


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


if __name__ == "__main__":
    unittest.main()
