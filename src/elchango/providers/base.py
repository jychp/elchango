"""Minimal provider boundary proven by the first real Cursor adapter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from elchango.models import ButtonIcon, ProviderCapability, ProviderSnapshot


class ProviderError(RuntimeError):
    """A provider could not complete a requested operation."""


@dataclass(frozen=True, slots=True)
class ProviderActionResult:
    """Provider-neutral result for one privileged native action."""

    accepted: bool
    verdict: str
    details: dict[str, object]


class AgentProvider(Protocol):
    """Read and safely act on one native agent provider."""

    provider_id: str
    display_name: str
    icon: ButtonIcon
    capabilities: frozenset[ProviderCapability]

    def snapshot(self) -> ProviderSnapshot:
        """Return one atomic, normalized provider snapshot."""

        ...

    def focus(self, native_session_id: str) -> ProviderActionResult:
        """Focus one exact native session when supported."""

        ...

    def open_new(self) -> ProviderActionResult:
        """Open a new native session composer when supported."""

        ...
