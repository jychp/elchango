from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from elchango.activity import ActivityStore
from elchango.providers.cursor import CursorProvider, CursorProviderError


class CursorProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.database = self.root / "state.vscdb"
        self.workspace_storage = self.root / "workspaceStorage"

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_snapshot_normalizes_selected_running_session(self) -> None:
        self._create_database()
        provider = CursorProvider(
            database=self.database,
            workspace_storage=self.workspace_storage,
            active_signal_ttl_ms=1_000,
            clock=lambda: 1_000,
        )

        snapshot = provider.snapshot()

        self.assertTrue(snapshot.read_only)
        self.assertEqual(snapshot.selected_session_id, "composer-1")
        self.assertEqual(len(snapshot.sessions), 1)
        session = snapshot.sessions[0]
        self.assertEqual(session.title, "Foundation work")
        self.assertEqual(session.workspace_path, "/tmp/elchango")
        self.assertEqual(session.last_activity_at_ms, 100)
        self.assertEqual(session.state, "working")
        self.assertEqual(session.confidence, "candidate")
        self.assertTrue(session.selected)

    def test_read_connection_uses_one_snapshot_transaction(self) -> None:
        self._create_database()
        provider = CursorProvider(
            database=self.database,
            workspace_storage=self.workspace_storage,
        )

        connection = provider._connect()
        try:
            self.assertTrue(connection.in_transaction)
        finally:
            connection.close()

    def test_record_purges_expired_unknown_sessions(self) -> None:
        store = ActivityStore(ttl_ms=10)
        store.record(
            {
                "hook_event_name": "beforeSubmitPrompt",
                "conversation_id": "unknown-old",
                "composer_mode": "plan",
            },
            observed_at_ms=1,
        )

        store.record(
            {
                "hook_event_name": "sessionStart",
                "conversation_id": "unknown-new",
            },
            observed_at_ms=12,
        )

        self.assertNotIn("unknown-old", store._signals)
        self.assertNotIn("unknown-old", store._composer_modes)

    def test_active_signal_expires_and_persisted_result_wins(self) -> None:
        self._create_database()
        now = [1_000]
        provider = CursorProvider(
            database=self.database,
            workspace_storage=self.workspace_storage,
            active_signal_ttl_ms=1_000,
            clock=lambda: now[0],
        )

        self.assertEqual(provider.snapshot().sessions[0].state, "working")

        now[0] = 2_000
        stale = provider.snapshot().sessions[0]
        self.assertEqual(stale.state, "idle")
        self.assertIn("stale", stale.state_detail)

        self._write_bubble(
            {
                "toolFormerData": {
                    "status": "completed",
                    "additionalData": {"status": "success"},
                }
            }
        )
        completed = provider.snapshot().sessions[0]
        self.assertEqual(completed.state, "idle")
        self.assertEqual(completed.confidence, "persisted")

        self._write_bubble(
            {
                "toolFormerData": {
                    "status": "completed",
                    "additionalData": {"status": "error"},
                }
            }
        )
        failed = provider.snapshot().sessions[0]
        self.assertEqual(failed.state, "idle")
        self.assertIn("stale", failed.state_detail)

    def test_recent_tool_error_remains_working_until_terminal_signal(self) -> None:
        self._create_database()
        provider = CursorProvider(
            database=self.database,
            workspace_storage=self.workspace_storage,
            active_signal_ttl_ms=1_000,
            clock=lambda: 1_000,
        )
        self._write_bubble(
            {
                "toolFormerData": {
                    "status": "completed",
                    "additionalData": {"status": "error"},
                }
            }
        )

        session = provider.snapshot().sessions[0]

        self.assertEqual(session.state, "working")
        self.assertIn("may continue", session.state_detail)

    def test_pending_plan_is_waiting_when_fresh(self) -> None:
        self._create_database()
        self._write_composer_data(
            {
                "fullConversationHeadersOnly": [{"bubbleId": "bubble-1"}],
                "hasPendingPlan": True,
            }
        )
        provider = CursorProvider(
            database=self.database,
            workspace_storage=self.workspace_storage,
            active_signal_ttl_ms=1_000,
            clock=lambda: 1_000,
        )

        session = provider.snapshot().sessions[0]

        self.assertEqual(session.state, "waiting")
        self.assertIn("plan", session.state_detail)

    def test_tool_authorization_overrides_working_hook(self) -> None:
        self._create_database()
        self._write_composer_data(
            {
                "fullConversationHeadersOnly": [{"bubbleId": "bubble-1"}],
                "hasBlockingPendingActions": True,
                "latestChatGenerationUUID": "generation-1",
            }
        )
        store = ActivityStore()
        store.record(
            {
                "hook_event_name": "beforeSubmitPrompt",
                "conversation_id": "composer-1",
                "generation_id": "generation-1",
            },
            observed_at_ms=1_000,
        )
        provider = CursorProvider(
            database=self.database,
            workspace_storage=self.workspace_storage,
            active_signal_ttl_ms=1_000,
            clock=lambda: 1_000,
            activity_store=store,
        )

        session = provider.snapshot().sessions[0]

        self.assertEqual(session.state, "waiting")
        self.assertEqual(session.confidence, "candidate")
        self.assertIn("user action", session.state_detail)

    def test_exact_hook_conversation_id_overrides_database_state(self) -> None:
        self._create_database()
        store = ActivityStore()
        now = [1_000]
        provider = CursorProvider(
            database=self.database,
            workspace_storage=self.workspace_storage,
            clock=lambda: now[0],
            activity_store=store,
        )
        store.record(
            {
                "hook_event_name": "beforeSubmitPrompt",
                "conversation_id": "composer-1",
            },
            observed_at_ms=now[0],
        )

        working = provider.snapshot().sessions[0]
        self.assertEqual(working.state, "working")
        self.assertEqual(working.state_detail, "Cursor prompt submitted")

        now[0] += 100
        store.record(
            {
                "hook_event_name": "stop",
                "conversation_id": "composer-1",
                "status": "completed",
            },
            observed_at_ms=now[0],
        )
        done = provider.snapshot().sessions[0]
        self.assertEqual(done.state, "done")
        self.assertEqual(done.confidence, "observed")

        now[0] += 100
        store.acknowledge("composer-1", observed_at_ms=now[0])
        acknowledged = provider.snapshot().sessions[0]
        self.assertEqual(acknowledged.state, "idle")
        self.assertEqual(
            acknowledged.state_detail,
            "completion acknowledged by focus",
        )

    def test_existing_selection_does_not_acknowledge_new_completion(self) -> None:
        store = ActivityStore()
        store.observe_selection("composer-1", observed_at_ms=100)
        store.record(
            {
                "hook_event_name": "stop",
                "conversation_id": "composer-1",
                "status": "completed",
            },
            observed_at_ms=200,
        )

        store.observe_selection("composer-1", observed_at_ms=300)

        state = store.state_for("composer-1", observed_at_ms=300)
        self.assertIsNotNone(state)
        self.assertEqual(state[0], "done")

    def test_selection_change_after_completion_acknowledges_it(self) -> None:
        store = ActivityStore()
        store.observe_selection("composer-2", observed_at_ms=100)
        store.record(
            {
                "hook_event_name": "stop",
                "conversation_id": "composer-1",
                "status": "completed",
            },
            observed_at_ms=200,
        )

        store.observe_selection("composer-1", observed_at_ms=300)

        state = store.state_for("composer-1", observed_at_ms=300)
        self.assertIsNotNone(state)
        self.assertEqual(state[0], "idle")

    def test_terminal_hook_error_requires_attention(self) -> None:
        store = ActivityStore()
        store.record(
            {
                "hook_event_name": "stop",
                "conversation_id": "composer-1",
                "status": "error",
            },
            observed_at_ms=100,
        )

        state = store.state_for("composer-1", observed_at_ms=100)

        self.assertIsNotNone(state)
        self.assertEqual(state[0], "waiting")

    def test_completed_plan_waits_for_approval(self) -> None:
        store = ActivityStore()
        store.record(
            {
                "hook_event_name": "beforeSubmitPrompt",
                "conversation_id": "composer-1",
                "generation_id": "generation-plan",
                "composer_mode": "plan",
            },
            observed_at_ms=100,
        )
        store.record(
            {
                "hook_event_name": "stop",
                "conversation_id": "composer-1",
                "generation_id": "generation-plan",
                "status": "completed",
            },
            observed_at_ms=200,
        )

        state = store.state_for("composer-1", observed_at_ms=200)

        self.assertIsNotNone(state)
        self.assertEqual(state[0], "waiting")
        self.assertIn("plan", state[2])

    def test_new_generation_invalidates_old_plan_waiting(self) -> None:
        store = ActivityStore()
        store.record(
            {
                "hook_event_name": "beforeSubmitPrompt",
                "conversation_id": "composer-1",
                "generation_id": "generation-plan",
                "composer_mode": "plan",
            },
            observed_at_ms=100,
        )
        store.record(
            {
                "hook_event_name": "stop",
                "conversation_id": "composer-1",
                "generation_id": "generation-plan",
                "status": "completed",
            },
            observed_at_ms=200,
        )

        state = store.state_for(
            "composer-1",
            observed_at_ms=300,
            current_generation_id="generation-next",
        )

        self.assertIsNone(state)

    def test_agent_prompt_replaces_plan_waiting_state(self) -> None:
        store = ActivityStore()
        store.record(
            {
                "hook_event_name": "beforeSubmitPrompt",
                "conversation_id": "composer-1",
                "composer_mode": "plan",
            },
            observed_at_ms=100,
        )
        store.record(
            {
                "hook_event_name": "stop",
                "conversation_id": "composer-1",
                "status": "completed",
            },
            observed_at_ms=200,
        )
        store.record(
            {
                "hook_event_name": "beforeSubmitPrompt",
                "conversation_id": "composer-1",
                "composer_mode": "agent",
            },
            observed_at_ms=300,
        )

        state = store.state_for("composer-1", observed_at_ms=300)

        self.assertIsNotNone(state)
        self.assertEqual(state[0], "working")

    def test_snapshot_rejects_unknown_schema(self) -> None:
        sqlite3.connect(self.database).close()
        provider = CursorProvider(
            database=self.database,
            workspace_storage=self.workspace_storage,
        )

        with self.assertRaisesRegex(CursorProviderError, "missing tables"):
            provider.snapshot()

    def _create_database(self) -> None:
        connection = sqlite3.connect(self.database)
        connection.executescript(
            """
            CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value BLOB);
            CREATE TABLE composerHeaders (
                composerId TEXT PRIMARY KEY,
                workspaceId TEXT,
                lastUpdatedAt INTEGER,
                recency INTEGER,
                isArchived INTEGER,
                isSubagent INTEGER,
                value BLOB
            );
            CREATE TABLE cursorDiskKV (key TEXT PRIMARY KEY, value BLOB);
            """
        )
        connection.executemany(
            "INSERT INTO ItemTable (key, value) VALUES (?, ?)",
            [
                ("cursor/glass.selectedAgent", "composer-1"),
                (
                    "glass.localAgentProjectMembership.v1",
                    json.dumps({"composer-1": {}}),
                ),
            ],
        )
        header = {
            "name": "Foundation work",
            "agentLocation": {
                "environment": {"uri": {"fsPath": "/tmp/elchango"}}
            },
        }
        connection.execute(
            """
            INSERT INTO composerHeaders (
                composerId, workspaceId, lastUpdatedAt, recency,
                isArchived, isSubagent, value
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "composer-1",
                "workspace-1",
                100,
                200,
                0,
                0,
                json.dumps(header),
            ),
        )
        composer_data = {
            "fullConversationHeadersOnly": [{"bubbleId": "bubble-1"}]
        }
        bubble_data = {"toolFormerData": {"status": "loading"}}
        connection.executemany(
            "INSERT INTO cursorDiskKV (key, value) VALUES (?, ?)",
            [
                ("composerData:composer-1", json.dumps(composer_data)),
                (
                    "bubbleId:composer-1:bubble-1",
                    json.dumps(bubble_data),
                ),
            ],
        )
        connection.commit()
        connection.close()

    def _write_bubble(self, payload: dict[str, object]) -> None:
        connection = sqlite3.connect(self.database)
        connection.execute(
            "UPDATE cursorDiskKV SET value = ? WHERE key = ?",
            (
                json.dumps(payload),
                "bubbleId:composer-1:bubble-1",
            ),
        )
        connection.commit()
        connection.close()

    def _write_composer_data(self, payload: dict[str, object]) -> None:
        connection = sqlite3.connect(self.database)
        connection.execute(
            "UPDATE cursorDiskKV SET value = ? WHERE key = ?",
            (
                json.dumps(payload),
                "composerData:composer-1",
            ),
        )
        connection.commit()
        connection.close()


if __name__ == "__main__":
    unittest.main()
