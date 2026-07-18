from __future__ import annotations

import unittest
from unittest import mock

from elchango.command_dispatch import dispatch_text
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
            result = dispatch_text("/compact", "expected.bundle")

        self.assertEqual(result.verdict, "DISPATCH_VERIFIED")
        self.assertTrue(result.executed)
        self.assertEqual(run.call_args.args[0][-1], "/compact")


if __name__ == "__main__":
    unittest.main()
