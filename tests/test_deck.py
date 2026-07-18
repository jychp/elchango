from __future__ import annotations

import unittest

from elchango.deck import DeckService, provider_positions
from elchango.models import AgentSession, ProviderSnapshot


class FakeProvider:
    provider_id = "test"
    display_name = "Test"
    icon = "cursor"
    capabilities = frozenset({"focus_session", "new_session"})

    def __init__(self, snapshot: ProviderSnapshot) -> None:
        self.current = snapshot

    def snapshot(self) -> ProviderSnapshot:
        return self.current


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def make_session(
    index: int,
    *,
    selected: bool = False,
    last_activity_at_ms: int | None = None,
) -> AgentSession:
    return AgentSession(
        provider_id="test",
        native_id=f"session-{index}",
        capabilities=FakeProvider.capabilities,
        icon="cursor",
        title=f"Session {index}",
        workspace_id=f"workspace-{index}",
        workspace_path=f"/tmp/repo-{index}",
        state="working" if selected else "idle",
        confidence="candidate" if selected else "persisted",
        state_detail="test state",
        selected=selected,
        last_activity_at_ms=(
            1_000 - index
            if last_activity_at_ms is None
            else last_activity_at_ms
        ),
    )


def make_snapshot(count: int) -> ProviderSnapshot:
    return ProviderSnapshot(
        provider_id="test",
        capabilities=FakeProvider.capabilities,
        observed_at_ms=123,
        selected_native_session_id="session-0" if count else None,
        sessions=tuple(
            make_session(index, selected=index == 0) for index in range(count)
        ),
        source="test",
    )


class DeckServiceTests(unittest.TestCase):
    def test_snapshot_always_contains_fifteen_ordered_buttons(self) -> None:
        service = DeckService(FakeProvider(make_snapshot(3)))

        snapshot = service.snapshot()

        self.assertEqual(len(snapshot.buttons), 15)
        self.assertEqual(
            [button.position for button in snapshot.buttons],
            list(range(15)),
        )
        self.assertEqual(
            [button.kind for button in snapshot.buttons[:3]],
            ["session", "session", "session"],
        )
        self.assertTrue(snapshot.buttons[0].selected)
        self.assertTrue(snapshot.buttons[0].enabled)
        self.assertEqual(snapshot.buttons[0].provider_id, "test")
        self.assertEqual(snapshot.buttons[0].session_id, "test:session-0")
        self.assertEqual(snapshot.buttons[0].icon, "cursor")
        self.assertEqual(snapshot.buttons[0].detail, "")
        self.assertTrue(
            all(button.kind == "empty" for button in snapshot.buttons[3:10])
        )
        self.assertTrue(all(button.enabled for button in snapshot.buttons[3:10]))
        self.assertEqual(
            [button.kind for button in snapshot.buttons[10:]],
            ["control", "empty", "empty", "empty", "control"],
        )
        self.assertEqual(snapshot.buttons[10].action, "refresh_sessions")
        self.assertEqual(snapshot.buttons[10].icon, "arrows-clockwise")
        self.assertEqual(snapshot.buttons[14].action, "choose_new_provider")
        self.assertEqual(snapshot.buttons[14].icon, "plus")
        self.assertIsNone(snapshot.buttons[14].provider_id)
        payload = snapshot.to_dict()
        self.assertNotIn("native_session_id", payload["buttons"][0])

    def test_provider_capabilities_disable_unsupported_actions(self) -> None:
        provider = FakeProvider(
            ProviderSnapshot(
                provider_id="readonly",
                capabilities=frozenset(),
                observed_at_ms=123,
                selected_native_session_id=None,
                sessions=(
                    AgentSession(
                        provider_id="readonly",
                        native_id="native-1",
                        capabilities=frozenset(),
                        icon="cursor",
                        title="Read only",
                        workspace_id="workspace-1",
                        workspace_path="/tmp/repo",
                        state="idle",
                        confidence="observed",
                        state_detail="test",
                        selected=False,
                        last_activity_at_ms=100,
                    ),
                ),
                source="test",
            )
        )
        provider.provider_id = "readonly"
        provider.capabilities = frozenset()

        snapshot = DeckService(provider).snapshot()

        self.assertFalse(snapshot.buttons[0].enabled)
        self.assertFalse(snapshot.buttons[1].enabled)
        self.assertFalse(snapshot.buttons[14].enabled)

    def test_revision_changes_only_when_provider_content_changes(self) -> None:
        provider = FakeProvider(make_snapshot(1))
        service = DeckService(provider)

        first = service.snapshot()
        provider.current = ProviderSnapshot(
            observed_at_ms=456,
            provider_id="test",
            capabilities=FakeProvider.capabilities,
            selected_native_session_id=provider.current.selected_native_session_id,
            sessions=provider.current.sessions,
            source=provider.current.source,
        )
        second = service.snapshot()
        provider.current = make_snapshot(2)
        third = service.snapshot()

        self.assertEqual(first.revision, second.revision)
        self.assertEqual(third.revision, second.revision + 1)

    def test_session_slots_survive_provider_reordering(self) -> None:
        provider = FakeProvider(make_snapshot(3))
        service = DeckService(provider)

        first = service.snapshot()
        provider.current = ProviderSnapshot(
            observed_at_ms=456,
            provider_id="test",
            capabilities=FakeProvider.capabilities,
            selected_native_session_id="session-0",
            sessions=tuple(reversed(provider.current.sessions)),
            source="test",
        )
        second = service.snapshot()

        self.assertEqual(
            [button.session_id for button in first.buttons[:3]],
            ["test:session-0", "test:session-1", "test:session-2"],
        )
        self.assertEqual(
            [button.session_id for button in second.buttons[:3]],
            ["test:session-0", "test:session-1", "test:session-2"],
        )

    def test_twenty_three_sessions_produce_three_pages(self) -> None:
        service = DeckService(FakeProvider(make_snapshot(23)))

        first = service.snapshot()
        second = service.next_page()
        third = service.next_page()

        self.assertEqual(
            [button.session_id for button in first.buttons[:10]],
            [f"test:session-{index}" for index in range(10)],
        )
        self.assertEqual(
            [button.session_id for button in second.buttons[:10]],
            [f"test:session-{index}" for index in range(10, 20)],
        )
        self.assertEqual(
            [button.session_id for button in third.buttons[:3]],
            ["test:session-20", "test:session-21", "test:session-22"],
        )
        self.assertEqual((first.page, first.page_count), (1, 3))
        self.assertEqual((second.page, second.page_count), (2, 3))
        self.assertEqual((third.page, third.page_count), (3, 3))
        self.assertEqual(first.buttons[14].action, "next_page")
        self.assertEqual(second.buttons[10].action, "previous_page")
        self.assertEqual(third.buttons[14].action, "choose_new_provider")

    def test_exactly_ten_sessions_offer_new_not_next(self) -> None:
        snapshot = DeckService(FakeProvider(make_snapshot(10))).snapshot()

        self.assertEqual(snapshot.page_count, 1)
        self.assertFalse(snapshot.has_next)
        self.assertEqual(snapshot.buttons[14].action, "choose_new_provider")
        self.assertTrue(snapshot.buttons[14].enabled)

    def test_equal_activity_dates_use_session_id_tie_breaker(self) -> None:
        provider = FakeProvider(
            ProviderSnapshot(
                observed_at_ms=123,
                provider_id="test",
                capabilities=FakeProvider.capabilities,
                selected_native_session_id=None,
                sessions=(
                    make_session(2, last_activity_at_ms=100),
                    make_session(1, last_activity_at_ms=100),
                ),
                source="test",
            )
        )

        snapshot = DeckService(provider).snapshot()

        self.assertEqual(
            [button.session_id for button in snapshot.buttons[:2]],
            ["test:session-1", "test:session-2"],
        )

    def test_sessions_from_multiple_providers_share_sorting_and_pagination(self) -> None:
        cursor = FakeProvider(
            ProviderSnapshot(
                provider_id="cursor",
                capabilities=FakeProvider.capabilities,
                observed_at_ms=100,
                selected_native_session_id="cursor-1",
                sessions=(
                    AgentSession(
                        provider_id="cursor",
                        native_id="cursor-1",
                        capabilities=FakeProvider.capabilities,
                        icon="cursor",
                        title="Cursor",
                        workspace_id="cursor-workspace",
                        workspace_path="/tmp/cursor",
                        state="working",
                        confidence="observed",
                        state_detail="test",
                        selected=True,
                        last_activity_at_ms=100,
                    ),
                ),
                source="cursor-test",
            )
        )
        cursor.provider_id = "cursor"
        claude = FakeProvider(
            ProviderSnapshot(
                provider_id="claude-code",
                capabilities=frozenset(),
                observed_at_ms=200,
                selected_native_session_id=None,
                sessions=tuple(
                    AgentSession(
                        provider_id="claude-code",
                        native_id=f"claude-{index}",
                        capabilities=frozenset(),
                        icon="claude",
                        title=f"Claude {index}",
                        workspace_id=f"claude-workspace-{index}",
                        workspace_path=f"/tmp/claude-{index}",
                        state="idle",
                        confidence="persisted",
                        state_detail="test",
                        selected=False,
                        last_activity_at_ms=200 - index,
                    )
                    for index in range(10)
                ),
                source="claude-test",
            )
        )
        claude.provider_id = "claude-code"
        claude.capabilities = frozenset()
        service = DeckService(
            {"cursor": cursor, "claude-code": claude},
            default_provider_id="cursor",
        )

        first = service.snapshot()
        second = service.next_page()

        self.assertEqual(first.page_count, 2)
        self.assertTrue(
            all(button.provider_id == "claude-code" for button in first.buttons[:10])
        )
        self.assertEqual(first.buttons[0].icon, "claude")
        self.assertFalse(first.buttons[0].enabled)
        self.assertEqual(second.buttons[0].session_id, "cursor:cursor-1")
        self.assertEqual(second.selected_session_id, "cursor:cursor-1")
        self.assertIsNone(second.buttons[1].provider_id)

    def test_new_sessions_append_without_reordering_existing_slots(self) -> None:
        provider = FakeProvider(make_snapshot(2))
        service = DeckService(provider)
        first = service.snapshot()
        provider.current = ProviderSnapshot(
            observed_at_ms=456,
            provider_id="test",
            capabilities=FakeProvider.capabilities,
            selected_native_session_id="session-2",
            sessions=(
                make_session(2, selected=True, last_activity_at_ms=2_000),
                *provider.current.sessions,
            ),
            source="test",
        )

        second = service.snapshot()

        self.assertEqual(
            [button.session_id for button in first.buttons[:2]],
            ["test:session-0", "test:session-1"],
        )
        self.assertEqual(
            [button.session_id for button in second.buttons[:3]],
            ["test:session-0", "test:session-1", "test:session-2"],
        )

    def test_removed_sessions_leave_holes_until_refresh(self) -> None:
        provider = FakeProvider(make_snapshot(3))
        service = DeckService(provider)
        service.snapshot()
        provider.current = ProviderSnapshot(
            observed_at_ms=456,
            provider_id="test",
            capabilities=FakeProvider.capabilities,
            selected_native_session_id="session-0",
            sessions=(make_session(0, selected=True), make_session(2)),
            source="test",
        )

        with_hole = service.snapshot()
        refreshed = service.refresh()

        self.assertIsNone(with_hole.buttons[1].session_id)
        self.assertEqual(with_hole.buttons[1].action, "choose_new_provider")
        self.assertTrue(with_hole.buttons[1].enabled)
        self.assertEqual(
            [button.session_id for button in refreshed.buttons[:2]],
            ["test:session-0", "test:session-2"],
        )

    def test_refresh_reorders_by_latest_activity_and_returns_to_page_one(self) -> None:
        provider = FakeProvider(make_snapshot(12))
        service = DeckService(provider)
        service.snapshot()
        service.next_page()
        provider.current = ProviderSnapshot(
            observed_at_ms=456,
            provider_id="test",
            capabilities=FakeProvider.capabilities,
            selected_native_session_id="session-11",
            sessions=tuple(
                make_session(
                    index,
                    selected=index == 11,
                    last_activity_at_ms=2_000 if index == 11 else 1_000 - index,
                )
                for index in range(12)
            ),
            source="test",
        )

        refreshed = service.refresh()

        self.assertEqual(refreshed.page, 1)
        self.assertEqual(refreshed.buttons[0].session_id, "test:session-11")

    def test_navigation_rejects_page_boundaries(self) -> None:
        service = DeckService(FakeProvider(make_snapshot(11)))
        service.snapshot()

        with self.assertRaisesRegex(ValueError, "first page"):
            service.previous_page()
        service.next_page()
        with self.assertRaisesRegex(ValueError, "last page"):
            service.next_page()

    def test_clients_navigate_independent_pages_and_revisions(self) -> None:
        service = DeckService(FakeProvider(make_snapshot(11)))

        web_first = service.snapshot("web")
        hardware_first = service.snapshot("streamdeck:serial-1")
        hardware_second = service.next_page("streamdeck:serial-1")
        web_second = service.snapshot("web")

        self.assertEqual(web_first.page, 1)
        self.assertEqual(web_second.page, 1)
        self.assertEqual(hardware_first.page, 1)
        self.assertEqual(hardware_second.page, 2)
        self.assertEqual(web_second.revision, web_first.revision)
        self.assertEqual(
            hardware_second.revision,
            hardware_first.revision + 1,
        )

    def test_new_session_provider_chooser_is_client_scoped(self) -> None:
        cursor = FakeProvider(make_snapshot(1))
        cursor.provider_id = "cursor"
        cursor.display_name = "Cursor"
        cursor.icon = "cursor"
        claude = FakeProvider(
            ProviderSnapshot(
                provider_id="claude-code",
                capabilities=FakeProvider.capabilities,
                observed_at_ms=123,
                selected_native_session_id=None,
                sessions=(),
                source="claude",
            )
        )
        claude.provider_id = "claude-code"
        claude.display_name = "Claude"
        claude.icon = "claude"
        service = DeckService({"cursor": cursor, "claude-code": claude})

        chooser = service.choose_new_provider("web")
        hardware = service.snapshot("streamdeck")

        self.assertEqual(
            [
                (button.position, button.provider_id, button.action)
                for button in chooser.buttons
                if button.enabled and button.provider_id is not None
            ],
            [
                (6, "cursor", "new_session"),
                (8, "claude-code", "new_session"),
            ],
        )
        self.assertEqual(chooser.buttons[10].action, "cancel_new_session")
        self.assertEqual(hardware.buttons[0].kind, "session")
        restored = service.cancel_new_session("web")
        self.assertEqual(restored.buttons[0].kind, "session")

    def test_provider_positions_follow_centered_dynamic_layout(self) -> None:
        self.assertEqual(provider_positions(1), (7,))
        self.assertEqual(provider_positions(2), (6, 8))
        self.assertEqual(provider_positions(3), (6, 7, 8))
        self.assertEqual(provider_positions(4), (5, 6, 8, 9))
        self.assertEqual(provider_positions(5), (5, 6, 7, 8, 9))
        with self.assertRaisesRegex(ValueError, "one and five"):
            provider_positions(6)

    def test_client_state_expires_and_returns_to_first_page(self) -> None:
        clock = FakeClock()
        service = DeckService(
            FakeProvider(make_snapshot(11)),
            client_state_ttl_seconds=10,
            clock=clock,
        )
        service.snapshot("hardware")
        service.next_page("hardware")

        clock.now = 10
        expired = service.snapshot("hardware")

        self.assertEqual(expired.page, 1)
        self.assertEqual(expired.revision, 1)
        self.assertEqual(service.active_client_count, 1)

    def test_client_state_bound_evicts_least_recently_used_client(self) -> None:
        clock = FakeClock()
        service = DeckService(
            FakeProvider(make_snapshot(11)),
            max_client_states=2,
            clock=clock,
        )
        service.snapshot("oldest")
        service.next_page("oldest")
        clock.now = 1
        service.snapshot("retained")
        clock.now = 2
        service.snapshot("new")

        recreated = service.snapshot("oldest")

        self.assertEqual(recreated.page, 1)
        self.assertEqual(recreated.revision, 1)
        self.assertEqual(service.active_client_count, 2)

    def test_client_ids_are_validated_at_every_entry_point(self) -> None:
        service = DeckService(FakeProvider(make_snapshot(11)))

        for operation in (
            service.snapshot,
            service.refresh,
            service.previous_page,
            service.next_page,
            service.choose_new_provider,
            service.cancel_new_session,
        ):
            with self.subTest(operation=operation.__name__):
                with self.assertRaisesRegex(ValueError, "client_id"):
                    operation("invalid client")

    def test_uncertain_and_error_states_use_four_color_model(self) -> None:
        unknown = make_session(1)
        error = make_session(2)
        provider = FakeProvider(
            ProviderSnapshot(
                observed_at_ms=123,
                provider_id="test",
                capabilities=FakeProvider.capabilities,
                selected_native_session_id=None,
                sessions=(
                    AgentSession(
                        provider_id=unknown.provider_id,
                        native_id=unknown.native_id,
                        capabilities=unknown.capabilities,
                        icon=unknown.icon,
                        title=unknown.title,
                        workspace_id=unknown.workspace_id,
                        workspace_path=unknown.workspace_path,
                        state="unknown",
                        confidence="unknown",
                        state_detail="uncertain",
                        selected=False,
                        last_activity_at_ms=unknown.last_activity_at_ms,
                    ),
                    AgentSession(
                        provider_id=error.provider_id,
                        native_id=error.native_id,
                        capabilities=error.capabilities,
                        icon=error.icon,
                        title=error.title,
                        workspace_id=error.workspace_id,
                        workspace_path=error.workspace_path,
                        state="error",
                        confidence="observed",
                        state_detail="terminal error",
                        selected=False,
                        last_activity_at_ms=error.last_activity_at_ms,
                    ),
                ),
                source="test",
            )
        )

        snapshot = DeckService(provider).snapshot()

        self.assertEqual(snapshot.buttons[0].color, "idle")
        self.assertEqual(snapshot.buttons[1].color, "waiting")


if __name__ == "__main__":
    unittest.main()
