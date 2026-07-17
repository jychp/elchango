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
        self.assertEqual(session.state, "working")
        self.assertEqual(session.confidence, "candidate")
        self.assertTrue(session.selected)

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
        self.assertEqual(completed.state, "done")
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
        self.assertEqual(failed.state, "error")
        self.assertEqual(failed.confidence, "observed")

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


if __name__ == "__main__":
    unittest.main()
