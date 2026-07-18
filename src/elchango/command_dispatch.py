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


def dispatch_text(
    command_text: str,
    expected_bundle_id: str,
    *,
    expected_input_marker: str | None = None,
    focus_shortcut: str | None = None,
    submit_count: int = 1,
) -> CommandDispatchResult:
    """Type and submit text only into a verified frontmost text input."""

    from elchango.providers.base import ProviderError

    if not command_text:
        raise ValueError("command text must be non-empty")
    if submit_count < 1:
        raise ValueError("submit count must be positive")
    if frontmost_bundle_id() != expected_bundle_id:
        raise ProviderError("command provider is not the frontmost application")
    started = time.monotonic()
    script = (
        "on run argv\n"
        'tell application "System Events"\n'
        "set targetProcess to first application process whose frontmost is true\n"
        "if bundle identifier of targetProcess is not item 5 of argv then "
        'error "frontmost bundle changed before dispatch"\n'
        'if item 2 of argv is not "" then\n'
        "keystroke item 2 of argv using command down\n"
        "delay 0.2\n"
        "end if\n"
        "set targetProcess to first application process whose frontmost is true\n"
        "if bundle identifier of targetProcess is not item 5 of argv then "
        'error "frontmost bundle changed before input"\n'
        'set focusedElement to value of attribute "AXFocusedUIElement" '
        "of targetProcess\n"
        'set focusedRole to value of attribute "AXRole" of focusedElement\n'
        'if focusedRole is not "AXTextArea" and focusedRole is not "AXTextField" '
        'and focusedRole is not "AXComboBox" then error "focused element is not '
        'a text input: " & focusedRole\n'
        'if (value of attribute "AXEnabled" of focusedElement) is not true then '
        'error "focused input is disabled"\n'
        'if item 3 of argv is not "" then\n'
        'set focusedClass to (value of attribute "AXDOMClassList" of '
        "focusedElement) as text\n"
        'if focusedClass is not item 3 of argv then error "focused input marker '
        'does not match"\n'
        "end if\n"
        'if (value of attribute "AXNumberOfCharacters" of focusedElement) '
        'is not 0 then error "focused input is not empty"\n'
        "keystroke item 1 of argv\n"
        "set submitCount to item 4 of argv as integer\n"
        "repeat submitCount times\n"
        "delay 0.5\n"
        "set targetProcess to first application process whose frontmost is true\n"
        "if bundle identifier of targetProcess is not item 5 of argv then "
        'error "frontmost bundle changed before submission"\n'
        'set focusedElement to value of attribute "AXFocusedUIElement" '
        "of targetProcess\n"
        'set focusedRole to value of attribute "AXRole" of focusedElement\n'
        'if focusedRole is not "AXTextArea" and focusedRole is not "AXTextField" '
        'and focusedRole is not "AXComboBox" then error "focused element changed '
        'before submission: " & focusedRole\n'
        'if (value of attribute "AXEnabled" of focusedElement) is not true then '
        'error "focused input is disabled before submission"\n'
        'if item 3 of argv is not "" then\n'
        'set focusedClass to (value of attribute "AXDOMClassList" of '
        "focusedElement) as text\n"
        'if focusedClass is not item 3 of argv then error "focused input marker '
        'changed before submission"\n'
        "end if\n"
        "key code 36\n"
        "end repeat\n"
        "end tell\n"
        "end run"
    )
    try:
        completed = subprocess.run(
            [
                "/usr/bin/osascript",
                "-e",
                script,
                "--",
                command_text,
                focus_shortcut or "",
                expected_input_marker or "",
                str(submit_count),
                expected_bundle_id,
            ],
            capture_output=True,
            text=True,
            timeout=8,
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


def dispatch_command_enter(
    expected_bundle_id: str,
    *,
    expected_input_marker: str | None = None,
    focus_shortcut: str | None = None,
) -> CommandDispatchResult:
    """Send Cmd+Enter once to a verified frontmost provider."""

    from elchango.providers.base import ProviderError

    if frontmost_bundle_id() != expected_bundle_id:
        raise ProviderError("command provider is not the frontmost application")
    started = time.monotonic()
    script = (
        "on run argv\n"
        'tell application "System Events"\n'
        "set targetProcess to first application process whose frontmost is true\n"
        "if bundle identifier of targetProcess is not item 3 of argv then "
        'error "frontmost bundle changed before dispatch"\n'
        'if item 1 of argv is not "" then\n'
        "keystroke item 1 of argv using command down\n"
        "delay 0.2\n"
        "end if\n"
        "set targetProcess to first application process whose frontmost is true\n"
        "if bundle identifier of targetProcess is not item 3 of argv then "
        'error "frontmost bundle changed before shortcut"\n'
        'if item 2 of argv is not "" then\n'
        'set focusedElement to value of attribute "AXFocusedUIElement" '
        "of targetProcess\n"
        'set focusedRole to value of attribute "AXRole" of focusedElement\n'
        'if focusedRole is not "AXTextArea" and focusedRole is not "AXTextField" '
        'and focusedRole is not "AXComboBox" then error "focused element is not '
        'a text input: " & focusedRole\n'
        'if (value of attribute "AXEnabled" of focusedElement) is not true then '
        'error "focused input is disabled"\n'
        'set focusedClass to (value of attribute "AXDOMClassList" of '
        "focusedElement) as text\n"
        'if focusedClass is not item 2 of argv then error "focused input marker '
        'does not match"\n'
        "end if\n"
        "key code 36 using command down\n"
        "end tell\n"
        "end run"
    )
    try:
        completed = subprocess.run(
            [
                "/usr/bin/osascript",
                "-e",
                script,
                "--",
                focus_shortcut or "",
                expected_input_marker or "",
                expected_bundle_id,
            ],
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
            message="Shortcut was sent but the provider lost foreground identity.",
        )
    return CommandDispatchResult(
        executed=True,
        elapsed_ms=round((time.monotonic() - started) * 1_000),
        verdict="DISPATCH_VERIFIED",
        message="Cmd+Enter was sent to the verified provider.",
    )
