from __future__ import annotations

import unittest
from unittest import mock

from elchango.activity import ActivityStore
from elchango.command_dispatch import CommandDispatchResult
from elchango.models import AgentSession, ProviderSnapshot
from elchango.providers.cursor_adapter import CursorAdapter


class StaticInventory:
    def snapshot(self) -> ProviderSnapshot:
        capabilities = frozenset({"focus_session", "new_session"})
        return ProviderSnapshot(
            provider_id="cursor",
            capabilities=capabilities,
            observed_at_ms=100,
            selected_native_session_id="target",
            sessions=(
                AgentSession(
                    provider_id="cursor",
                    native_id="target",
                    capabilities=capabilities,
                    icon="cursor",
                    title="Target",
                    workspace_id="workspace",
                    workspace_path="/tmp/workspace",
                    state="idle",
                    confidence="persisted",
                    state_detail="test",
                    selected=True,
                    last_activity_at_ms=100,
                ),
            ),
            source="test",
        )


class CursorAdapterCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = CursorAdapter(
            inventory=StaticInventory(),  # type: ignore[arg-type]
            focus_controller=mock.Mock(),
            launch_controller=mock.Mock(),
            activity_store=ActivityStore(),
        )
        self.dispatched = CommandDispatchResult(
            executed=True,
            elapsed_ms=5,
            verdict="DISPATCH_VERIFIED",
            message="sent",
        )

    def test_snapshot_advertises_all_proven_cursor_commands(self) -> None:
        snapshot = self.adapter.snapshot()

        self.assertIn("execute_command", snapshot.capabilities)
        self.assertEqual(
            snapshot.sessions[0].commands,
            frozenset({"accept", "create_pr", "commit_push", "compact"}),
        )

    def test_text_commands_focus_and_verify_cursor_composer(self) -> None:
        with (
            mock.patch(
                "elchango.providers.cursor_adapter.frontmost_bundle_id",
                return_value=self.adapter.bundle_id,
            ),
            mock.patch(
                "elchango.providers.cursor_adapter.dispatch_text",
                return_value=self.dispatched,
            ) as dispatch,
        ):
            result = self.adapter.execute_command("target", "compact")

        self.assertTrue(result.accepted)
        dispatch.assert_called_once_with(
            "/summarize",
            self.adapter.bundle_id,
            expected_input_marker=self.adapter.input_marker,
            focus_shortcut="l",
            submit_count=2,
        )

    def test_open_pr_uses_cursor_instruction_text(self) -> None:
        with (
            mock.patch(
                "elchango.providers.cursor_adapter.frontmost_bundle_id",
                return_value=self.adapter.bundle_id,
            ),
            mock.patch(
                "elchango.providers.cursor_adapter.dispatch_text",
                return_value=self.dispatched,
            ) as dispatch,
        ):
            result = self.adapter.execute_command("target", "create_pr")

        self.assertTrue(result.accepted)
        dispatch.assert_called_once_with(
            "Open a pull request for the current branch.",
            self.adapter.bundle_id,
            expected_input_marker=self.adapter.input_marker,
            focus_shortcut="l",
            submit_count=1,
        )

    def test_accept_uses_command_enter_without_text(self) -> None:
        with (
            mock.patch(
                "elchango.providers.cursor_adapter.frontmost_bundle_id",
                return_value=self.adapter.bundle_id,
            ),
            mock.patch(
                "elchango.providers.cursor_adapter.dispatch_command_enter",
                return_value=self.dispatched,
            ) as dispatch,
            mock.patch(
                "elchango.providers.cursor_adapter.dispatch_text"
            ) as dispatch_text,
        ):
            result = self.adapter.execute_command("target", "accept")

        self.assertTrue(result.accepted)
        dispatch.assert_called_once_with(
            self.adapter.bundle_id,
            expected_input_marker=self.adapter.input_marker,
            focus_shortcut="l",
        )
        dispatch_text.assert_not_called()

    def test_rejects_command_for_unselected_target(self) -> None:
        with (
            mock.patch(
                "elchango.providers.cursor_adapter.frontmost_bundle_id",
                return_value=self.adapter.bundle_id,
            ),
            mock.patch(
                "elchango.providers.cursor_adapter.dispatch_text"
            ) as dispatch,
        ):
            result = self.adapter.execute_command("other", "create_pr")

        self.assertFalse(result.accepted)
        self.assertEqual(result.verdict, "TARGET_UNVERIFIED")
        dispatch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
