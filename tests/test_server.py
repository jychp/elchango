from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from elchango.activity import ActivityStore
from elchango.deck import DeckService
from elchango.models import AgentSession, ProviderSnapshot
from elchango.server import DeckHTTPServer, DeckRequestHandler


class StaticProvider:
    def snapshot(self) -> ProviderSnapshot:
        session = AgentSession(
            id="session-1",
            title="Server test",
            workspace_id="workspace-1",
            workspace_path="/tmp/server-test",
            state="idle",
            confidence="persisted",
            state_detail="test",
            selected=True,
            updated_at_ms=1,
        )
        return ProviderSnapshot(
            observed_at_ms=123,
            selected_session_id=session.id,
            sessions=(session,),
            source="test",
        )


class DeckServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        assets = Path(self.temporary_directory.name)
        (assets / "index.html").write_text("<main>deck</main>", encoding="utf-8")
        self.server = DeckHTTPServer(("127.0.0.1", 0), DeckRequestHandler)
        self.server.deck_service = DeckService(StaticProvider())
        self.server.activity_store = ActivityStore()
        self.server.assets = assets
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            kwargs={"poll_interval": 0.01},
            daemon=True,
        )
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1)
        self.temporary_directory.cleanup()

    def test_snapshot_endpoint_returns_fixed_deck(self) -> None:
        with urllib.request.urlopen(
            f"{self.base_url}/api/snapshot",
            timeout=2,
        ) as response:
            payload = json.load(response)

        self.assertEqual(response.status, 200)
        self.assertEqual(len(payload["buttons"]), 15)
        self.assertEqual(payload["selected_session_id"], "session-1")
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_static_root_is_served_with_security_headers(self) -> None:
        with urllib.request.urlopen(self.base_url, timeout=2) as response:
            body = response.read().decode("utf-8")

        self.assertEqual(body, "<main>deck</main>")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])

    def test_post_requests_are_disabled(self) -> None:
        request = urllib.request.Request(
            f"{self.base_url}/api/intent",
            data=b"{}",
            method="POST",
        )

        with self.assertRaises(urllib.error.HTTPError) as context:
            urllib.request.urlopen(request, timeout=2)

        error = context.exception
        self.assertEqual(error.code, 405)
        try:
            payload = json.loads(error.read())
        finally:
            error.close()
        self.assertIn("not enabled", payload["error"])

    def test_cursor_hook_endpoint_accepts_lifecycle_metadata(self) -> None:
        request = urllib.request.Request(
            f"{self.base_url}/api/hooks/cursor",
            data=json.dumps(
                {
                    "hook_event_name": "beforeSubmitPrompt",
                    "conversation_id": "session-1",
                    "prompt": "must not be stored",
                }
            ).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        with urllib.request.urlopen(request, timeout=2) as response:
            payload = json.load(response)

        self.assertEqual(response.status, 202)
        self.assertTrue(payload["accepted"])
        state = self.server.activity_store.state_for(
            "session-1",
            observed_at_ms=time.time_ns() // 1_000_000,
        )
        self.assertIsNotNone(state)
        self.assertEqual(state[0], "working")


if __name__ == "__main__":
    unittest.main()
