"""Cursor provider adapter combining inventory with verified native actions."""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from typing import ClassVar

from elchango.activity import ActivityStore
from elchango.command_dispatch import dispatch_text, frontmost_bundle_id
from elchango.focus import CursorFocusController
from elchango.launch import CursorLaunchController
from elchango.models import (
    ButtonIcon,
    CommandId,
    ProviderCapability,
    ProviderSnapshot,
)
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
    display_name: ClassVar[str] = "Cursor"
    icon: ClassVar[ButtonIcon] = "cursor"
    capabilities: ClassVar[frozenset[ProviderCapability]] = frozenset(
        {"focus_session", "new_session"}
    )
    bundle_id: ClassVar[str] = "com.todesktop.230313mzl4w4u92"
    command_recipes: ClassVar[dict[CommandId, str]] = {}

    def snapshot(self) -> ProviderSnapshot:
        snapshot = self.inventory.snapshot()
        commands = frozenset(self.command_recipes)
        command_capability = {"execute_command"} if commands else set()
        return replace(
            snapshot,
            capabilities=self.capabilities | command_capability,
            sessions=tuple(
                replace(
                    session,
                    capabilities=session.capabilities | command_capability,
                    commands=commands,
                )
                for session in snapshot.sessions
            ),
        )

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

    def is_frontmost(self) -> bool:
        return frontmost_bundle_id() == self.bundle_id

    def execute_command(
        self,
        native_session_id: str,
        command_id: CommandId,
    ) -> ProviderActionResult:
        recipe = self.command_recipes.get(command_id)
        if recipe is None:
            return ProviderActionResult(
                accepted=False,
                verdict="COMMAND_UNSUPPORTED",
                details={"message": f"Cursor does not support {command_id}."},
            )
        before = self.snapshot()
        if (
            before.selected_native_session_id != native_session_id
            or not self.is_frontmost()
        ):
            return ProviderActionResult(
                accepted=False,
                verdict="TARGET_UNVERIFIED",
                details={
                    "message": "Cursor target is not uniquely selected and frontmost."
                },
            )
        result = dispatch_text(recipe, self.bundle_id)
        after = self.snapshot()
        accepted = (
            result.verdict == "DISPATCH_VERIFIED"
            and after.selected_native_session_id == native_session_id
            and self.is_frontmost()
        )
        return ProviderActionResult(
            accepted=accepted,
            verdict=result.verdict if accepted else "DISPATCH_UNVERIFIED",
            details={
                **result.to_dict(),
                "session_id": native_session_id,
                "command_id": command_id,
            },
        )
