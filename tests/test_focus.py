from __future__ import annotations

import unittest

from elchango.focus import (
    _SidebarCandidate,
    _candidate_section,
    _shortcut_index,
)


class CursorFocusTests(unittest.TestCase):
    def test_sidebar_shortcut_uses_one_based_agent_index(self) -> None:
        self.assertEqual(
            _shortcut_index(
                "session-2",
                ("session-1", "session-2", "session-3"),
            ),
            2,
        )

    def test_absent_agent_has_no_sidebar_shortcut(self) -> None:
        self.assertIsNone(
            _shortcut_index(
                "missing",
                ("session-1", "session-2"),
            )
        )

    def test_tenth_agent_continues_after_ninth_shortcut(self) -> None:
        sessions = tuple(f"session-{index}" for index in range(1, 11))
        self.assertEqual(_shortcut_index("session-10", sessions), 10)

    def test_repository_section_matches_source_repository_name(self) -> None:
        candidate = _SidebarCandidate(
            session_id="session-1",
            updated_at=2,
            created_at=1,
            workspace_id="workspace-1",
            repository_name="elchango",
        )
        self.assertEqual(
            _candidate_section(
                candidate,
                [
                    "repo:github.com/jychp/elchango",
                    "workspace:workspace-1",
                ],
            ),
            "repo:github.com/jychp/elchango",
        )

    def test_workspace_section_is_conservative_repository_fallback(self) -> None:
        candidate = _SidebarCandidate(
            session_id="session-1",
            updated_at=2,
            created_at=1,
            workspace_id="workspace-1",
            repository_name="shared-name",
        )
        self.assertEqual(
            _candidate_section(
                candidate,
                [
                    "repo:github.com/one/shared-name",
                    "repo:github.com/two/shared-name",
                    "workspace:workspace-1",
                ],
            ),
            "workspace:workspace-1",
        )


if __name__ == "__main__":
    unittest.main()
