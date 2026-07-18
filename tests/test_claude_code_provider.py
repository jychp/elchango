from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from elchango.claude_activity import ClaudeActivityStore
from elchango.providers.base import ProviderActionResult
from elchango.providers.claude_code import (
    ClaudeCodeProvider,
    ClaudeCodeProviderError,
)


class ClaudeCodeProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.desktop_root = root / "desktop"
        self.projects_root = root / "projects"
        self.records = self.desktop_root / "account" / "workspace"
        self.desktop_config = root / "claude_desktop_config.json"
        self.records.mkdir(parents=True)
        self.projects_root.mkdir()
        self._write_shortcut_config([])
        self.activity = ClaudeActivityStore(
            terminal_deadline_ms=100,
            ttl_ms=1_000,
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_snapshot_includes_persistent_non_archived_sessions(self) -> None:
        self._write_session("local_a", "cli-a", activity=200, title="Claude A")
        self._write_session(
            "local_archived",
            "cli-archived",
            activity=300,
            archived=True,
        )
        provider = self._provider()

        snapshot = provider.snapshot()

        self.assertEqual(snapshot.provider_id, "claude-code")
        self.assertEqual(snapshot.capabilities, frozenset({"focus_session"}))
        self.assertEqual(len(snapshot.sessions), 1)
        session = snapshot.sessions[0]
        self.assertEqual(session.id, "claude-code:local_a")
        self.assertEqual(session.capabilities, frozenset({"focus_session"}))
        self.assertEqual(session.icon, "claude")
        self.assertEqual(session.title, "Claude A")
        self.assertEqual(session.workspace_path, "/tmp/worktree-local_a")
        self.assertEqual(session.state, "idle")
        self.assertEqual(session.confidence, "persisted")
        self.assertFalse(session.selected)

    def test_focus_uses_persisted_shortcut_order_and_exact_verification(self) -> None:
        self._write_session(
            "local_target",
            "cli-target",
            activity=200,
            last_focused_at=100,
        )
        self._write_shortcut_config(["local_target"])
        provider = self._provider()
        self.assertEqual(
            provider.snapshot().sessions[0].capabilities,
            frozenset({"focus_session"}),
        )

        def focus_target(index: int) -> None:
            self.assertEqual(index, 1)
            self._write_session(
                "local_target",
                "cli-target",
                activity=200,
                last_focused_at=500,
            )

        with (
            mock.patch(
                "elchango.providers.claude_code._send_focus_shortcut",
                side_effect=focus_target,
            ),
            mock.patch(
                "elchango.providers.claude_code._claude_is_frontmost",
                return_value=True,
            ),
        ):
            result = provider.focus("local_target")

        self.assertIsInstance(result, ProviderActionResult)
        self.assertTrue(result.accepted)
        self.assertEqual(result.verdict, "FOCUS_VERIFIED")
        self.assertEqual(result.details["shortcut_index"], 1)

    def test_fresh_hook_state_overlays_persistent_inventory(self) -> None:
        self._write_session("local_a", "cli-a", activity=200)
        self.activity.record(
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": "cli-a",
                "cwd": "/tmp/worktree-local_a",
                "transcript_path": "/tmp/cli-a.jsonl",
            },
            100,
        )
        provider = self._provider()

        with mock.patch(
            "elchango.providers.claude_code.time.time_ns",
            return_value=150_000_000,
        ):
            session = provider.snapshot().sessions[0]

        self.assertEqual(session.state, "working")
        self.assertEqual(session.confidence, "observed")

    def test_changed_record_invalidates_metadata_cache(self) -> None:
        path = self._write_session("local_a", "cli-a", activity=200, title="Before")
        provider = self._provider()
        self.assertEqual(provider.snapshot().sessions[0].title, "Before")

        self._write_session("local_a", "cli-a", activity=300, title="After")
        path.touch()
        session = provider.snapshot().sessions[0]

        self.assertEqual(session.title, "After")
        self.assertEqual(session.last_activity_at_ms, 300)

    def test_missing_schema_field_fails_explicitly(self) -> None:
        path = self.records / "local_broken.json"
        path.write_text(
            json.dumps({"sessionId": "local_broken", "isArchived": False}),
            encoding="utf-8",
        )
        provider = self._provider()

        with self.assertRaisesRegex(
            ClaudeCodeProviderError,
            "missing required fields",
        ):
            provider.snapshot()

    def test_missing_transcript_fails_explicitly(self) -> None:
        self._write_session(
            "local_a",
            "cli-a",
            activity=200,
            create_transcript=False,
        )

        with self.assertRaisesRegex(
            ClaudeCodeProviderError,
            "transcript mapping must be unique",
        ):
            self._provider().snapshot()

    def _provider(self) -> ClaudeCodeProvider:
        return ClaudeCodeProvider(
            desktop_sessions_root=self.desktop_root,
            projects_root=self.projects_root,
            desktop_config=self.desktop_config,
            activity_store=self.activity,
        )

    def _write_session(
        self,
        desktop_id: str,
        cli_id: str,
        *,
        activity: int,
        title: str | None = "Claude session",
        archived: bool = False,
        create_transcript: bool = True,
        last_focused_at: int | None = None,
    ) -> Path:
        cwd = f"/tmp/worktree-{desktop_id}"
        value = {
            "sessionId": desktop_id,
            "cliSessionId": cli_id,
            "cwd": cwd,
            "originCwd": "/tmp/repository",
            "createdAt": 100,
            "lastActivityAt": activity,
            **(
                {}
                if last_focused_at is None
                else {"lastFocusedAt": last_focused_at}
            ),
            "isArchived": archived,
            "title": title,
            "messages": [{"sensitive": "not parsed"}],
        }
        path = self.records / f"{desktop_id}.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        if create_transcript:
            project = self.projects_root / cwd.replace("/", "-")
            project.mkdir(exist_ok=True)
            (project / f"{cli_id}.jsonl").write_text("{}\n", encoding="utf-8")
        return path

    def _write_shortcut_config(self, starred: list[str]) -> None:
        value = {
            "preferences": {
                "epitaxyPrefs": {
                    "starred-local-code-sessions": starred,
                    "dframe-local-slice": {
                        "customGroupAssignments": {},
                        "customGroupOrder": {},
                    },
                }
            }
        }
        self.desktop_config.write_text(json.dumps(value), encoding="utf-8")


class ClaudeActivityStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = ClaudeActivityStore(
            terminal_deadline_ms=100,
            ttl_ms=1_000,
        )
        self.base_payload = {
            "session_id": "cli-a",
            "cwd": "/tmp/repository",
            "transcript_path": "/tmp/cli-a.jsonl",
        }

    def record(self, event: str, **extra: object) -> None:
        self.store.record(
            {
                **self.base_payload,
                "hook_event_name": event,
                **extra,
            },
            100,
        )

    def test_documented_events_map_to_deck_states(self) -> None:
        cases = (
            ("UserPromptSubmit", {}, "working"),
            ("Notification", {"notification_type": "permission_prompt"}, "waiting"),
            ("Stop", {}, "done"),
            ("StopFailure", {}, "error"),
            ("SessionEnd", {}, "idle"),
        )
        for event, extra, expected in cases:
            with self.subTest(event=event):
                self.record(event, **extra)
                self.assertEqual(self.store.state_for("cli-a", 150)[0], expected)

    def test_missing_terminal_event_becomes_explicitly_degraded(self) -> None:
        self.record("UserPromptSubmit")

        state = self.store.state_for("cli-a", 201)

        self.assertEqual(state[0], "unknown")
        self.assertEqual(state[1], "unknown")
        self.assertIn("terminal event was not observed", state[2])

    def test_unknown_notification_does_not_invent_waiting(self) -> None:
        self.record("Notification", notification_type="auth_success")

        state = self.store.state_for("cli-a", 150)

        self.assertEqual(state[0], "idle")
        self.assertEqual(state[1], "candidate")


if __name__ == "__main__":
    unittest.main()
