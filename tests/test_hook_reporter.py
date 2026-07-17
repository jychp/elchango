from __future__ import annotations

import io
import json
import unittest
from unittest.mock import patch

from elchango.hook_reporter import report_hook


class HookReporterTests(unittest.TestCase):
    def test_reporter_forwards_only_lifecycle_metadata(self) -> None:
        input_stream = io.StringIO(
            json.dumps(
                {
                    "hook_event_name": "beforeSubmitPrompt",
                    "conversation_id": "composer-1",
                    "generation_id": "generation-1",
                    "prompt": "private prompt",
                    "user_email": "private@example.com",
                }
            )
        )
        output_stream = io.StringIO()

        with patch("urllib.request.urlopen") as urlopen:
            report_hook(input_stream, output_stream)

        request = urlopen.call_args.args[0]
        forwarded = json.loads(request.data)
        self.assertEqual(
            forwarded,
            {
                "hook_event_name": "beforeSubmitPrompt",
                "conversation_id": "composer-1",
                "generation_id": "generation-1",
            },
        )
        self.assertEqual(output_stream.getvalue(), "{}\n")

    def test_reporter_fails_open_for_invalid_input(self) -> None:
        output_stream = io.StringIO()

        report_hook(io.StringIO("not json"), output_stream)

        self.assertEqual(output_stream.getvalue(), "{}\n")


if __name__ == "__main__":
    unittest.main()
