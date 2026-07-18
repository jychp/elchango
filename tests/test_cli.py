from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from elchango.cli import main
from elchango.providers.cursor import CursorProviderError


class CLITests(unittest.TestCase):
    def test_api_only_serve_does_not_require_compiled_assets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing_assets = Path(directory) / "missing"
            with (
                patch("elchango.cli.CursorProvider") as provider_class,
                patch("elchango.cli.ClaudeCodeProvider") as claude_provider_class,
                patch("elchango.cli.CursorFocusController"),
                patch("elchango.cli.CursorLaunchController"),
                patch(
                    "elchango.cli._application_bundle_available",
                    return_value=True,
                ),
                patch("elchango.cli.serve") as serve,
            ):
                result = main(
                    [
                        "serve",
                        "--api-only",
                        "--assets",
                        str(missing_assets),
                    ]
                )

        self.assertEqual(result, 0)
        provider_class.return_value.snapshot.assert_called_once_with()
        claude_provider_class.return_value.snapshot.assert_called_once_with()
        self.assertTrue(serve.call_args.args[-1])

    def test_one_unavailable_provider_does_not_block_the_other(self) -> None:
        with (
            patch("elchango.cli.CursorProvider") as cursor_provider_class,
            patch("elchango.cli.ClaudeCodeProvider") as claude_provider_class,
            patch("elchango.cli.CursorFocusController"),
            patch("elchango.cli.CursorLaunchController"),
            patch(
                "elchango.cli._application_bundle_available",
                return_value=True,
            ),
            patch("elchango.cli.serve") as serve,
        ):
            cursor_provider_class.return_value.snapshot.side_effect = (
                CursorProviderError("Cursor database missing")
            )
            claude_provider_class.return_value.provider_id = "claude-code"
            result = main(["serve", "--api-only"])

        self.assertEqual(result, 0)
        claude_provider_class.return_value.snapshot.assert_called_once_with()
        providers = serve.call_args.args[2]
        self.assertEqual(list(providers), ["claude-code"])
        self.assertEqual(
            serve.call_args.kwargs["unavailable_providers"],
            {"cursor": "Cursor database missing"},
        )

    def test_no_installed_provider_still_starts_empty_deck(self) -> None:
        with (
            patch(
                "elchango.cli._application_bundle_available",
                return_value=False,
            ),
            patch("elchango.cli.serve") as serve,
        ):
            result = main(["serve", "--api-only"])

        self.assertEqual(result, 0)
        service = serve.call_args.args[0]
        snapshot = service.snapshot()
        self.assertEqual(snapshot.selected_session_id, None)
        self.assertTrue(all(not button.enabled for button in snapshot.buttons[:10]))
        self.assertFalse(snapshot.buttons[14].enabled)
        self.assertEqual(
            set(serve.call_args.kwargs["unavailable_providers"]),
            {"cursor", "claude-code"},
        )


if __name__ == "__main__":
    unittest.main()
