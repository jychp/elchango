"""Conservative macOS text dispatch for a verified provider session."""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass


TEXT_INPUT_ROLES = {"AXTextArea", "AXTextField", "AXComboBox"}


@dataclass(frozen=True, slots=True)
class CommandDispatchResult:
    executed: bool
    elapsed_ms: int
    verdict: str
    message: str

    def to_dict(self) -> dict[str, object]:
        return {
            "executed": self.executed,
            "elapsed_ms": self.elapsed_ms,
            "verdict": self.verdict,
            "message": self.message,
        }


def frontmost_bundle_id() -> str | None:
    """Return the frontmost macOS application bundle identifier."""

    try:
        completed = subprocess.run(
            [
                "/usr/bin/osascript",
                "-e",
                (
                    'tell application "System Events" to get bundle identifier '
                    "of first application process whose frontmost is true"
                ),
            ],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return completed.stdout.strip() if completed.returncode == 0 else None


def dispatch_text(command_text: str, expected_bundle_id: str) -> CommandDispatchResult:
    """Type and submit text only into a verified frontmost text input."""

    from elchango.providers.base import ProviderError

    if not command_text:
        raise ValueError("command text must be non-empty")
    if frontmost_bundle_id() != expected_bundle_id:
        raise ProviderError("command provider is not the frontmost application")
    started = time.monotonic()
    script = (
        "on run argv\n"
        'tell application "System Events"\n'
        "set targetProcess to first application process whose frontmost is true\n"
        "tell targetProcess\n"
        "set focusedRole to role of focused UI element\n"
        'if focusedRole is not "AXTextArea" and focusedRole is not "AXTextField" '
        'and focusedRole is not "AXComboBox" then error "focused element is not '
        'a text input: " & focusedRole\n'
        "keystroke item 1 of argv\n"
        "key code 36\n"
        "end tell\n"
        "end tell\n"
        "end run"
    )
    try:
        completed = subprocess.run(
            ["/usr/bin/osascript", "-e", script, command_text],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ProviderError(f"command dispatch failed: {error}") from error
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "no output"
        raise ProviderError(f"command dispatch rejected: {detail}")
    if frontmost_bundle_id() != expected_bundle_id:
        return CommandDispatchResult(
            executed=True,
            elapsed_ms=round((time.monotonic() - started) * 1_000),
            verdict="DISPATCH_UNVERIFIED",
            message="Text was submitted but the provider lost foreground identity.",
        )
    return CommandDispatchResult(
        executed=True,
        elapsed_ms=round((time.monotonic() - started) * 1_000),
        verdict="DISPATCH_VERIFIED",
        message="Command text was submitted to the verified provider input.",
    )
