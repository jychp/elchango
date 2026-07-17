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
from elchango.focus import FocusResult
from elchango.models import AgentSession, ProviderSnapshot
from elchango.server import DeckHTTPServer, DeckRequestHandler


class StaticProvider:
    def __init__(self, count: int = 1) -> None:
        self.count = count

    def snapshot(self) -> ProviderSnapshot:
        sessions = tuple(
            AgentSession(
                id=f"session-{index}",
                title=f"Server test {index}",
                workspace_id=f"workspace-{index}",
                workspace_path="/tmp/server-test",
                state="idle",
                confidence="persisted",
                state_detail="test",
                selected=index == 1,
                last_activity_at_ms=100 - index,
            )
            for index in range(1, self.count + 1)
        )
        return ProviderSnapshot(
            observed_at_ms=123,
            selected_session_id="session-1",
            sessions=sessions,
            source="test",
        )


class FakeFocusController:
    def __init__(self) -> None:
        self.focused_session_id: str | None = None

    def focus(self, session_id: str) -> FocusResult:
        self.focused_session_id = session_id
        return FocusResult(
            session_id=session_id,
            selected_before="other-session",
            selected_after=session_id,
            strategy="sidebar_shortcut",
            shortcut_index=1,
            executed=True,
            cursor_frontmost=True,
            elapsed_ms=10,
            verdict="FOCUS_VERIFIED",
            message="verified",
        )


class DeckServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        assets = Path(self.temporary_directory.name)
        (assets / "index.html").write_text("<main>deck</main>", encoding="utf-8")
        self.server = DeckHTTPServer(("127.0.0.1", 0), DeckRequestHandler)
        self.server.deck_service = DeckService(StaticProvider())
        self.server.activity_store = ActivityStore()
        self.focus_controller = FakeFocusController()
        self.server.focus_controller = self.focus_controller
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

    def test_unknown_post_requests_are_disabled(self) -> None:
        request = urllib.request.Request(
            f"{self.base_url}/api/action",
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

    def test_focus_endpoint_verifies_current_session_button(self) -> None:
        now = time.time_ns() // 1_000_000
        self.server.activity_store.record(
            {
                "hook_event_name": "stop",
                "conversation_id": "session-1",
                "status": "completed",
            },
            observed_at_ms=now,
        )
        request = urllib.request.Request(
            f"{self.base_url}/api/focus",
            data=json.dumps(
                {
                    "session_id": "session-1",
                    "revision": 1,
                }
            ).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        with urllib.request.urlopen(request, timeout=2) as response:
            payload = json.load(response)

        self.assertEqual(response.status, 200)
        self.assertEqual(payload["verdict"], "FOCUS_VERIFIED")
        self.assertEqual(self.focus_controller.focused_session_id, "session-1")
        state = self.server.activity_store.state_for(
            "session-1",
            observed_at_ms=now + 1,
        )
        self.assertIsNotNone(state)
        self.assertEqual(state[0], "idle")

    def test_focus_endpoint_accepts_stale_revision_when_session_remains(self) -> None:
        request = urllib.request.Request(
            f"{self.base_url}/api/focus",
            data=json.dumps(
                {
                    "session_id": "session-1",
                    "revision": 0,
                }
            ).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        with urllib.request.urlopen(request, timeout=2) as response:
            payload = json.load(response)

        self.assertEqual(response.status, 200)
        self.assertEqual(payload["verdict"], "FOCUS_VERIFIED")
        self.assertEqual(self.focus_controller.focused_session_id, "session-1")

    def test_refresh_intent_accepts_stale_revision(self) -> None:
        request = urllib.request.Request(
            f"{self.base_url}/api/intent",
            data=json.dumps(
                {
                    "button_id": "control:refresh",
                    "revision": 0,
                }
            ).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        with urllib.request.urlopen(request, timeout=2) as response:
            payload = json.load(response)

        self.assertEqual(response.status, 200)
        self.assertTrue(payload["accepted"])
        self.assertEqual(payload["action"], "refresh_sessions")
        self.assertEqual(payload["snapshot"]["page"], 1)

    def test_next_and_previous_intents_change_server_page(self) -> None:
        self.server.deck_service = DeckService(StaticProvider(count=11))
        self.server.deck_service.snapshot()

        next_payload = self._post_intent("control:next")
        previous_payload = self._post_intent("control:previous")

        self.assertEqual(next_payload["snapshot"]["page"], 2)
        self.assertEqual(previous_payload["snapshot"]["page"], 1)

    def test_available_session_slot_rejects_new_until_launch_is_verified(self) -> None:
        request = urllib.request.Request(
            f"{self.base_url}/api/intent",
            data=json.dumps(
                {
                    "button_id": "empty:1",
                    "revision": 1,
                }
            ).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        with self.assertRaises(urllib.error.HTTPError) as context:
            urllib.request.urlopen(request, timeout=2)

        self.assertEqual(context.exception.code, 409)
        context.exception.close()

    def test_disabled_empty_control_rejects_intent(self) -> None:
        request = urllib.request.Request(
            f"{self.base_url}/api/intent",
            data=json.dumps(
                {
                    "button_id": "empty:11",
                    "revision": 1,
                }
            ).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        with self.assertRaises(urllib.error.HTTPError) as context:
            urllib.request.urlopen(request, timeout=2)

        self.assertEqual(context.exception.code, 409)
        context.exception.close()

    def _post_intent(self, button_id: str) -> dict[str, object]:
        request = urllib.request.Request(
            f"{self.base_url}/api/intent",
            data=json.dumps(
                {
                    "button_id": button_id,
                    "revision": 0,
                }
            ).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            return json.load(response)


if __name__ == "__main__":
    unittest.main()
