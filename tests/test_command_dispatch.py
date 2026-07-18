from __future__ import annotations

import unittest
from unittest import mock

from elchango.command_dispatch import dispatch_command_enter, dispatch_text
from elchango.providers.base import ProviderError


class CommandDispatchTests(unittest.TestCase):
    def test_rejects_dispatch_when_provider_is_not_frontmost(self) -> None:
        with mock.patch(
            "elchango.command_dispatch.frontmost_bundle_id",
            return_value="other.bundle",
        ):
            with self.assertRaisesRegex(ProviderError, "not the frontmost"):
                dispatch_text("/compact", "expected.bundle")

    def test_reports_verified_dispatch_after_text_input_submission(self) -> None:
        completed = mock.Mock(returncode=0, stdout="", stderr="")
        with (
            mock.patch(
                "elchango.command_dispatch.frontmost_bundle_id",
                return_value="expected.bundle",
            ),
            mock.patch(
                "elchango.command_dispatch.subprocess.run",
                return_value=completed,
            ) as run,
        ):
            result = dispatch_text(
                "/summarize",
                "expected.bundle",
                expected_input_marker="cursor-composer",
                focus_shortcut="l",
                submit_count=2,
            )

        self.assertEqual(result.verdict, "DISPATCH_VERIFIED")
        self.assertTrue(result.executed)
        arguments = run.call_args.args[0]
        self.assertEqual(arguments[-4:], ["/summarize", "l", "cursor-composer", "2"])
        self.assertIn("repeat submitCount times", arguments[2])

    def test_dispatches_command_enter_shortcut_once(self) -> None:
        completed = mock.Mock(returncode=0, stdout="", stderr="")
        with (
            mock.patch(
                "elchango.command_dispatch.frontmost_bundle_id",
                return_value="expected.bundle",
            ),
            mock.patch(
                "elchango.command_dispatch.subprocess.run",
                return_value=completed,
            ) as run,
        ):
            result = dispatch_command_enter(
                "expected.bundle",
                expected_input_marker="cursor-composer",
                focus_shortcut="l",
            )

        self.assertEqual(result.verdict, "DISPATCH_VERIFIED")
        self.assertTrue(result.executed)
        arguments = run.call_args.args[0]
        self.assertEqual(arguments[-2:], ["l", "cursor-composer"])
        self.assertIn("key code 36 using command down", arguments[2])


if __name__ == "__main__":
    unittest.main()
