from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from elchango.cli import main


class CLITests(unittest.TestCase):
    def test_api_only_serve_does_not_require_compiled_assets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing_assets = Path(directory) / "missing"
            with (
                patch("elchango.cli.CursorProvider") as provider_class,
                patch("elchango.cli.CursorFocusController"),
                patch("elchango.cli.CursorLaunchController"),
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
        self.assertTrue(serve.call_args.args[-1])


if __name__ == "__main__":
    unittest.main()
