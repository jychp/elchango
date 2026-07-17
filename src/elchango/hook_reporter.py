"""Fail-open reporter for Cursor lifecycle command hooks."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, TextIO


DEFAULT_HOOK_ENDPOINT = "http://127.0.0.1:8765/api/hooks/cursor"
FORWARDED_FIELDS = {
    "hook_event_name",
    "conversation_id",
    "generation_id",
    "status",
}


def report_hook(
    input_stream: TextIO,
    output_stream: TextIO,
    endpoint: str = DEFAULT_HOOK_ENDPOINT,
) -> None:
    """Forward sanitized lifecycle metadata and always allow Cursor to continue."""

    try:
        incoming = json.load(input_stream)
        if not isinstance(incoming, dict):
            raise ValueError("hook input must be an object")
        payload: dict[str, Any] = {
            key: incoming[key]
            for key in FORWARDED_FIELDS
            if key in incoming
        }
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=0.5):
            pass
    except (
        json.JSONDecodeError,
        OSError,
        urllib.error.URLError,
        ValueError,
    ):
        pass
    output_stream.write("{}\n")
