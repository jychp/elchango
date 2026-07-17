"""Open Cursor's New Agent view without submitting a prompt."""

from __future__ import annotations

import ctypes
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass
from typing import Callable

from elchango.providers.cursor import CursorProviderError


@dataclass(frozen=True, slots=True)
class LaunchResult:
    """Result of one user-requested New Agent view opening."""

    executed: bool
    cursor_frontmost: bool | None
    elapsed_ms: int
    verdict: str
    message: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class CursorLaunchController:
    """Serialize native requests to open Cursor's blank New Agent view."""

    def __init__(
        self,
        *,
        activate: Callable[[], None] | None = None,
        frontmost_application: Callable[[], str | None] | None = None,
        send_shortcuts: Callable[[], None] | None = None,
    ) -> None:
        self._activate = activate or _activate_cursor
        self._frontmost_application = (
            frontmost_application or _frontmost_application
        )
        self._send_shortcuts = send_shortcuts or _send_new_agent_shortcuts
        self._lock = threading.Lock()

    def open_new(self) -> LaunchResult:
        with self._lock:
            return self._open_new_locked()

    def _open_new_locked(self) -> LaunchResult:
        started = time.monotonic()
        if sys.platform != "darwin":
            raise CursorProviderError(
                "Cursor New Agent view is supported only on macOS"
            )
        self._activate()
        time.sleep(0.2)
        frontmost = self._frontmost_application()
        if frontmost != "Cursor":
            return _result(
                started,
                False,
                False if frontmost is not None else None,
                "CURSOR_NOT_FOREGROUND",
                "Cursor was not foreground, so no New Agent shortcut was sent.",
            )

        self._send_shortcuts()
        time.sleep(0.1)
        frontmost = self._frontmost_application()
        if frontmost != "Cursor":
            return _result(
                started,
                True,
                False if frontmost is not None else None,
                "CURSOR_LOST_FOREGROUND",
                "Cursor lost foreground after the New Agent shortcut.",
            )
        return _result(
            started,
            True,
            True,
            "NEW_AGENT_VIEW_REQUESTED",
            "Cursor is foreground and the blank New Agent view was requested.",
        )


def _result(
    started: float,
    executed: bool,
    cursor_frontmost: bool | None,
    verdict: str,
    message: str,
) -> LaunchResult:
    return LaunchResult(
        executed=executed,
        cursor_frontmost=cursor_frontmost,
        elapsed_ms=round((time.monotonic() - started) * 1_000),
        verdict=verdict,
        message=message,
    )


def _activate_cursor() -> None:
    result = subprocess.run(
        ["open", "-a", "Cursor"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "no output"
        raise CursorProviderError(
            f"Cursor activation failed with exit {result.returncode}: {detail}"
        )


def _frontmost_application() -> str | None:
    result = subprocess.run(
        [
            "osascript",
            "-e",
            (
                'tell application "System Events" to get name of first '
                "application process whose frontmost is true"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=3,
    )
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


def _send_new_agent_shortcuts() -> None:
    application_services = ctypes.CDLL(
        "/System/Library/Frameworks/ApplicationServices.framework/"
        "ApplicationServices"
    )
    core_foundation = ctypes.CDLL(
        "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
    )
    create_event = application_services.CGEventCreateKeyboardEvent
    create_event.argtypes = [ctypes.c_void_p, ctypes.c_uint16, ctypes.c_bool]
    create_event.restype = ctypes.c_void_p
    set_flags = application_services.CGEventSetFlags
    set_flags.argtypes = [ctypes.c_void_p, ctypes.c_uint64]
    post_event = application_services.CGEventPost
    post_event.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
    release = core_foundation.CFRelease
    release.argtypes = [ctypes.c_void_p]

    def post_key(key_code: int, key_down: bool, flags: int) -> None:
        event = create_event(None, key_code, key_down)
        if not event:
            raise CursorProviderError("macOS failed to create a keyboard event")
        try:
            set_flags(event, flags)
            post_event(0, event)
        finally:
            release(event)

    command_key = 55
    option_key = 58
    n_key = 45
    command_flag = 1 << 20
    option_flag = 1 << 19

    post_key(option_key, True, option_flag)
    post_key(command_key, True, option_flag | command_flag)
    try:
        post_key(n_key, True, option_flag | command_flag)
        time.sleep(0.1)
        post_key(n_key, False, option_flag | command_flag)
    finally:
        post_key(command_key, False, option_flag)
        post_key(option_key, False, 0)

    time.sleep(0.5)
    if _frontmost_application() != "Cursor":
        raise CursorProviderError(
            "Cursor lost foreground before the New Agent shortcut"
        )
    post_key(command_key, True, command_flag)
    try:
        post_key(n_key, True, command_flag)
        time.sleep(0.1)
        post_key(n_key, False, command_flag)
    finally:
        post_key(command_key, False, 0)
