"""Minimal provider boundary proven by the first real Cursor adapter."""

from __future__ import annotations

from typing import Protocol

from elchango.models import ProviderSnapshot


class AgentProvider(Protocol):
    """Read the current set of native agent sessions."""

    def snapshot(self) -> ProviderSnapshot:
        """Return one atomic, normalized provider snapshot."""

        ...
