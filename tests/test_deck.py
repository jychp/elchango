from __future__ import annotations

import unittest

from elchango.deck import DeckService
from elchango.models import AgentSession, ProviderSnapshot


class FakeProvider:
    def __init__(self, snapshot: ProviderSnapshot) -> None:
        self.current = snapshot

    def snapshot(self) -> ProviderSnapshot:
        return self.current


def make_session(
    index: int,
    *,
    selected: bool = False,
    last_activity_at_ms: int | None = None,
) -> AgentSession:
    return AgentSession(
        id=f"session-{index}",
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
        observed_at_ms=123,
        selected_session_id="session-0" if count else None,
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
        self.assertTrue(
            all(button.kind == "empty" for button in snapshot.buttons[3:10])
        )
        self.assertTrue(
            all(not button.enabled for button in snapshot.buttons[3:10])
        )
        self.assertEqual(
            [button.kind for button in snapshot.buttons[10:]],
            ["control", "empty", "empty", "empty", "control"],
        )
        self.assertEqual(snapshot.buttons[10].action, "refresh_sessions")
        self.assertEqual(snapshot.buttons[14].action, "new_session")

    def test_revision_changes_only_when_provider_content_changes(self) -> None:
        provider = FakeProvider(make_snapshot(1))
        service = DeckService(provider)

        first = service.snapshot()
        provider.current = ProviderSnapshot(
            observed_at_ms=456,
            selected_session_id=provider.current.selected_session_id,
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
            selected_session_id="session-0",
            sessions=tuple(reversed(provider.current.sessions)),
            source="test",
        )
        second = service.snapshot()

        self.assertEqual(
            [button.session_id for button in first.buttons[:3]],
            ["session-0", "session-1", "session-2"],
        )
        self.assertEqual(
            [button.session_id for button in second.buttons[:3]],
            ["session-0", "session-1", "session-2"],
        )

    def test_twenty_three_sessions_produce_three_pages(self) -> None:
        service = DeckService(FakeProvider(make_snapshot(23)))

        first = service.snapshot()
        second = service.next_page()
        third = service.next_page()

        self.assertEqual(
            [button.session_id for button in first.buttons[:10]],
            [f"session-{index}" for index in range(10)],
        )
        self.assertEqual(
            [button.session_id for button in second.buttons[:10]],
            [f"session-{index}" for index in range(10, 20)],
        )
        self.assertEqual(
            [button.session_id for button in third.buttons[:3]],
            ["session-20", "session-21", "session-22"],
        )
        self.assertEqual((first.page, first.page_count), (1, 3))
        self.assertEqual((second.page, second.page_count), (2, 3))
        self.assertEqual((third.page, third.page_count), (3, 3))
        self.assertEqual(first.buttons[14].action, "next_page")
        self.assertEqual(second.buttons[10].action, "previous_page")
        self.assertEqual(third.buttons[14].action, "new_session")

    def test_exactly_ten_sessions_offer_new_not_next(self) -> None:
        snapshot = DeckService(FakeProvider(make_snapshot(10))).snapshot()

        self.assertEqual(snapshot.page_count, 1)
        self.assertFalse(snapshot.has_next)
        self.assertEqual(snapshot.buttons[14].action, "new_session")
        self.assertFalse(snapshot.buttons[14].enabled)

    def test_equal_activity_dates_use_session_id_tie_breaker(self) -> None:
        provider = FakeProvider(
            ProviderSnapshot(
                observed_at_ms=123,
                selected_session_id=None,
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
            ["session-1", "session-2"],
        )

    def test_new_sessions_append_without_reordering_existing_slots(self) -> None:
        provider = FakeProvider(make_snapshot(2))
        service = DeckService(provider)
        first = service.snapshot()
        provider.current = ProviderSnapshot(
            observed_at_ms=456,
            selected_session_id="session-2",
            sessions=(
                make_session(2, selected=True, last_activity_at_ms=2_000),
                *provider.current.sessions,
            ),
            source="test",
        )

        second = service.snapshot()

        self.assertEqual(
            [button.session_id for button in first.buttons[:2]],
            ["session-0", "session-1"],
        )
        self.assertEqual(
            [button.session_id for button in second.buttons[:3]],
            ["session-0", "session-1", "session-2"],
        )

    def test_removed_sessions_leave_holes_until_refresh(self) -> None:
        provider = FakeProvider(make_snapshot(3))
        service = DeckService(provider)
        service.snapshot()
        provider.current = ProviderSnapshot(
            observed_at_ms=456,
            selected_session_id="session-0",
            sessions=(make_session(0, selected=True), make_session(2)),
            source="test",
        )

        with_hole = service.snapshot()
        refreshed = service.refresh()

        self.assertIsNone(with_hole.buttons[1].session_id)
        self.assertEqual(with_hole.buttons[1].action, "new_session")
        self.assertFalse(with_hole.buttons[1].enabled)
        self.assertEqual(
            [button.session_id for button in refreshed.buttons[:2]],
            ["session-0", "session-2"],
        )

    def test_refresh_reorders_by_latest_activity_and_returns_to_page_one(self) -> None:
        provider = FakeProvider(make_snapshot(12))
        service = DeckService(provider)
        service.snapshot()
        service.next_page()
        provider.current = ProviderSnapshot(
            observed_at_ms=456,
            selected_session_id="session-11",
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
        self.assertEqual(refreshed.buttons[0].session_id, "session-11")

    def test_navigation_rejects_page_boundaries(self) -> None:
        service = DeckService(FakeProvider(make_snapshot(11)))
        service.snapshot()

        with self.assertRaisesRegex(ValueError, "first page"):
            service.previous_page()
        service.next_page()
        with self.assertRaisesRegex(ValueError, "last page"):
            service.next_page()

    def test_uncertain_and_error_states_use_four_color_model(self) -> None:
        unknown = make_session(1)
        error = make_session(2)
        provider = FakeProvider(
            ProviderSnapshot(
                observed_at_ms=123,
                selected_session_id=None,
                sessions=(
                    AgentSession(
                        id=unknown.id,
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
                        id=error.id,
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
