"""Cursor provider adapter combining inventory with verified native actions."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import ClassVar

from elchango.activity import ActivityStore
from elchango.focus import CursorFocusController
from elchango.launch import CursorLaunchController
from elchango.models import ProviderCapability, ProviderSnapshot
from elchango.providers.base import ProviderActionResult
from elchango.providers.cursor import CursorProvider


@dataclass(frozen=True, slots=True)
class CursorAdapter:
    """Expose Cursor through the provider-neutral service boundary."""

    inventory: CursorProvider
    focus_controller: CursorFocusController
    launch_controller: CursorLaunchController
    activity_store: ActivityStore

    provider_id: ClassVar[str] = "cursor"
    capabilities: ClassVar[frozenset[ProviderCapability]] = frozenset(
        {"focus_session", "new_session"}
    )

    def snapshot(self) -> ProviderSnapshot:
        return self.inventory.snapshot()

    def focus(self, native_session_id: str) -> ProviderActionResult:
        result = self.focus_controller.focus(native_session_id)
        accepted = result.verdict == "FOCUS_VERIFIED"
        if accepted:
            self.activity_store.acknowledge(
                native_session_id,
                time.time_ns() // 1_000_000,
            )
        return ProviderActionResult(
            accepted=accepted,
            verdict=result.verdict,
            details=result.to_dict(),
        )

    def open_new(self) -> ProviderActionResult:
        result = self.launch_controller.open_new()
        return ProviderActionResult(
            accepted=result.verdict == "NEW_AGENT_VIEW_REQUESTED",
            verdict=result.verdict,
            details=result.to_dict(),
        )
