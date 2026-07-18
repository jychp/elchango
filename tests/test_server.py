from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from elchango.activity import ActivityStore
from elchango.claude_activity import ClaudeActivityStore
from elchango.deck import DeckService
from elchango.focus import FocusResult
from elchango.launch import LaunchResult
from elchango.models import AgentSession, ProviderSnapshot
from elchango.providers.cursor_adapter import CursorAdapter
from elchango.server import DeckHTTPServer, DeckRequestHandler, serve


class StaticProvider:
    provider_id = "cursor"
    capabilities = frozenset({"focus_session", "new_session"})

    def __init__(self, count: int = 1) -> None:
        self.count = count

    def snapshot(self) -> ProviderSnapshot:
        sessions = tuple(
            AgentSession(
                provider_id=self.provider_id,
                native_id=f"session-{index}",
                capabilities=self.capabilities,
                icon="cursor",
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
            provider_id=self.provider_id,
            capabilities=self.capabilities,
            observed_at_ms=123,
            selected_native_session_id="session-1",
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


class FakeLaunchController:
    def __init__(self) -> None:
        self.open_count = 0

    def open_new(self) -> LaunchResult:
        self.open_count += 1
        return LaunchResult(
            executed=True,
            cursor_frontmost=True,
            elapsed_ms=10,
            verdict="NEW_AGENT_VIEW_REQUESTED",
            message="requested",
        )


class DeckServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        assets = Path(self.temporary_directory.name)
        (assets / "index.html").write_text("<main>deck</main>", encoding="utf-8")
        self.server = DeckHTTPServer(("127.0.0.1", 0), DeckRequestHandler)
        self.server.activity_store = ActivityStore()
        self.claude_activity_store = ClaudeActivityStore()
        self.server.hook_recorders = {
            "cursor": self.server.activity_store.record,
            "claude-code": self.claude_activity_store.record,
        }
        self.focus_controller = FakeFocusController()
        self.launch_controller = FakeLaunchController()
        self.cursor = CursorAdapter(
            inventory=StaticProvider(),  # type: ignore[arg-type]
            focus_controller=self.focus_controller,  # type: ignore[arg-type]
            launch_controller=self.launch_controller,  # type: ignore[arg-type]
            activity_store=self.server.activity_store,
        )
        self.server.deck_service = DeckService(self.cursor)
        self.server.providers = {self.cursor.provider_id: self.cursor}
        self.server.assets = assets
        self.server.api_only = False
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
        self.assertEqual(payload["selected_session_id"], "cursor:session-1")
        self.assertEqual(payload["buttons"][0]["provider_id"], "cursor")
        self.assertNotIn("native_session_id", payload["buttons"][0])
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_health_reports_provider_capabilities(self) -> None:
        with urllib.request.urlopen(
            f"{self.base_url}/api/health",
            timeout=2,
        ) as response:
            payload = json.load(response)

        self.assertTrue(payload["focus_enabled"])
        self.assertTrue(payload["launch_enabled"])
        self.assertEqual(
            payload["providers"]["cursor"],
            ["focus_session", "new_session"],
        )

    def test_snapshot_clients_keep_independent_pages(self) -> None:
        self.server.deck_service = DeckService(StaticProvider(count=11))

        activated = self._post_activate(
            "streamdeck:serial-1",
            "control:next",
        )
        hardware = self._get_snapshot("streamdeck:serial-1")
        web = self._get_snapshot("web")

        self.assertEqual(activated["snapshot"]["page"], 2)
        self.assertEqual(hardware["page"], 2)
        self.assertEqual(web["page"], 1)
        previous = self._post_activate(
            "streamdeck:serial-1",
            "control:previous",
        )
        self.assertEqual(previous["snapshot"]["page"], 1)

    def test_snapshot_rejects_invalid_client_ids(self) -> None:
        for client_id in ("", "has space", "é", "x" * 129):
            with self.subTest(client_id=client_id):
                encoded = urllib.parse.urlencode({"client_id": client_id})
                with self.assertRaises(urllib.error.HTTPError) as context:
                    urllib.request.urlopen(
                        f"{self.base_url}/api/snapshot?{encoded}",
                        timeout=2,
                    )
                self.assertEqual(context.exception.code, 400)
                context.exception.close()

    def test_serve_rejects_non_loopback_host(self) -> None:
        with self.assertRaisesRegex(ValueError, "loopback"):
            serve(
                self.server.deck_service,
                self.server.activity_store,
                self.server.providers,
                Path(self.temporary_directory.name),
                "0.0.0.0",
                0,
            )

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

    def test_claude_hook_endpoint_accepts_official_lifecycle_metadata(self) -> None:
        request = urllib.request.Request(
            f"{self.base_url}/api/hooks/claude-code",
            data=json.dumps(
                {
                    "hook_event_name": "Notification",
                    "session_id": "claude-session-1",
                    "cwd": "/tmp/claude",
                    "transcript_path": "/tmp/claude-session-1.jsonl",
                    "notification_type": "permission_prompt",
                    "message": "must not be stored",
                }
            ).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        with urllib.request.urlopen(request, timeout=2) as response:
            payload = json.load(response)

        self.assertEqual(response.status, 202)
        self.assertEqual(payload["provider_id"], "claude-code")
        state = self.claude_activity_store.state_for(
            "claude-session-1",
            observed_at_ms=time.time_ns() // 1_000_000,
        )
        self.assertIsNotNone(state)
        self.assertEqual(state[0], "waiting")

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

    def test_available_session_slot_requests_new_agent_view_once(self) -> None:
        payload = self._post_intent("empty:1")

        self.assertTrue(payload["accepted"])
        self.assertEqual(payload["action"], "new_session")
        self.assertEqual(self.launch_controller.open_count, 1)

    def test_unified_activate_focuses_session_and_acknowledges_completion(
        self,
    ) -> None:
        now = time.time_ns() // 1_000_000
        self.server.activity_store.record(
            {
                "hook_event_name": "stop",
                "conversation_id": "session-1",
                "status": "completed",
            },
            observed_at_ms=now,
        )

        payload = self._post_activate("hardware", "session:cursor:session-1")

        self.assertTrue(payload["accepted"])
        self.assertEqual(payload["action"], "focus_session")
        self.assertEqual(payload["focus"]["verdict"], "FOCUS_VERIFIED")
        self.assertEqual(self.focus_controller.focused_session_id, "session-1")
        state = self.server.activity_store.state_for(
            "session-1",
            observed_at_ms=now + 1,
        )
        self.assertIsNotNone(state)
        self.assertEqual(state[0], "idle")

    def test_unified_activate_dispatches_refresh_and_new_controls(self) -> None:
        refreshed = self._post_activate("hardware", "control:refresh")
        launched = self._post_activate("hardware", "control:new")
        launched_from_empty = self._post_activate("hardware", "empty:1")

        self.assertTrue(refreshed["accepted"])
        self.assertEqual(refreshed["action"], "refresh_sessions")
        self.assertEqual(refreshed["snapshot"]["page"], 1)
        self.assertTrue(launched["accepted"])
        self.assertEqual(launched["action"], "new_session")
        self.assertEqual(launched["launch"]["verdict"], "NEW_AGENT_VIEW_REQUESTED")
        self.assertTrue(launched_from_empty["accepted"])
        self.assertEqual(launched_from_empty["action"], "new_session")
        self.assertEqual(self.launch_controller.open_count, 2)

    def test_unified_activate_rejects_invalid_client_id(self) -> None:
        request = urllib.request.Request(
            f"{self.base_url}/api/activate",
            data=json.dumps(
                {
                    "client_id": "invalid client",
                    "button_id": "control:refresh",
                    "revision": 1,
                }
            ).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        with self.assertRaises(urllib.error.HTTPError) as context:
            urllib.request.urlopen(request, timeout=2)

        self.assertEqual(context.exception.code, 400)
        context.exception.close()

    def test_api_only_mode_serves_api_and_rejects_web_assets(self) -> None:
        self.server.api_only = True

        snapshot = self._get_snapshot("web")
        self.assertEqual(len(snapshot["buttons"]), 15)
        with self.assertRaises(urllib.error.HTTPError) as context:
            urllib.request.urlopen(self.base_url, timeout=2)
        error = context.exception
        self.assertEqual(error.code, 404)
        try:
            payload = json.loads(error.read())
        finally:
            error.close()
        self.assertIn("API-only", payload["error"])

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

    def _post_activate(
        self,
        client_id: str,
        button_id: str,
    ) -> dict[str, object]:
        request = urllib.request.Request(
            f"{self.base_url}/api/activate",
            data=json.dumps(
                {
                    "client_id": client_id,
                    "button_id": button_id,
                    "revision": 0,
                }
            ).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            return json.load(response)

    def _get_snapshot(self, client_id: str) -> dict[str, object]:
        encoded = urllib.parse.urlencode({"client_id": client_id})
        with urllib.request.urlopen(
            f"{self.base_url}/api/snapshot?{encoded}",
            timeout=2,
        ) as response:
            return json.load(response)


if __name__ == "__main__":
    unittest.main()
