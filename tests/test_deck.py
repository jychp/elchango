from __future__ import annotations

import unittest

from elchango.deck import DeckService
from elchango.models import AgentSession, ProviderSnapshot


class FakeProvider:
    def __init__(self, snapshot: ProviderSnapshot) -> None:
        self.current = snapshot

    def snapshot(self) -> ProviderSnapshot:
        return self.current


def make_session(index: int, *, selected: bool = False) -> AgentSession:
    return AgentSession(
        id=f"session-{index}",
        title=f"Session {index}",
        workspace_id=f"workspace-{index}",
        workspace_path=f"/tmp/repo-{index}",
        state="working" if selected else "idle",
        confidence="candidate" if selected else "persisted",
        state_detail="test state",
        selected=selected,
        updated_at_ms=1_000 - index,
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
        self.assertTrue(
            all(button.kind == "empty" for button in snapshot.buttons[3:10])
        )
        self.assertTrue(
            all(button.kind == "control" for button in snapshot.buttons[10:])
        )

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

    def test_session_overflow_is_truncated_and_control_row_stays_fixed(self) -> None:
        service = DeckService(FakeProvider(make_snapshot(12)))

        snapshot = service.snapshot()

        self.assertEqual(
            [button.session_id for button in snapshot.buttons[:10]],
            [f"session-{index}" for index in range(10)],
        )
        self.assertEqual(snapshot.buttons[10].action, "new_session")
        self.assertEqual(snapshot.buttons[11].action, "focus_session")
        self.assertEqual(snapshot.buttons[14].action, "stop_session")


if __name__ == "__main__":
    unittest.main()
