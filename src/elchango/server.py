"""Loopback HTTP server for the local elChango web deck."""

from __future__ import annotations

import ipaddress
import json
import mimetypes
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from elchango.activity import ActivityStore
from elchango.deck import DEFAULT_CLIENT_ID, DeckService, validate_client_id
from elchango.focus import CursorFocusController
from elchango.launch import CursorLaunchController
from elchango.providers.cursor import CursorProviderError


class DeckHTTPServer(ThreadingHTTPServer):
    """Carry immutable application dependencies into request handlers."""

    daemon_threads = True
    deck_service: DeckService
    activity_store: ActivityStore
    focus_controller: CursorFocusController
    launch_controller: CursorLaunchController
    assets: Path
    api_only: bool = False


class DeckRequestHandler(BaseHTTPRequestHandler):
    """Serve versioned snapshots and the compiled Svelte application."""

    server: DeckHTTPServer

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            self._send_json(
                HTTPStatus.OK,
                {
                    "status": "ok",
                    "focus_enabled": True,
                    "launch_enabled": True,
                    "actions_enabled": False,
                },
            )
            return
        if parsed.path == "/api/snapshot":
            self._serve_snapshot(parsed.query)
            return
        if parsed.path.startswith("/api/"):
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        self._serve_asset(parsed.path)

    def do_POST(self) -> None:
        if urlparse(self.path).path == "/api/hooks/cursor":
            self._receive_cursor_hook()
            return
        if urlparse(self.path).path == "/api/focus":
            self._focus_session()
            return
        if urlparse(self.path).path == "/api/intent":
            self._activate_intent()
            return
        if urlparse(self.path).path == "/api/activate":
            self._activate_button()
            return
        self._send_json(
            HTTPStatus.METHOD_NOT_ALLOWED,
            {
                "error": "actions are not enabled in the foundation slice",
            },
        )

    def _focus_session(self) -> None:
        payload = self._read_json_payload(max_bytes=4_096)
        if payload is None:
            return
        session_id = payload.get("session_id")
        revision = payload.get("revision")
        if not isinstance(session_id, str) or not session_id:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "session_id must be a non-empty string"},
            )
            return
        if isinstance(revision, bool) or not isinstance(revision, int):
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "revision must be an integer"},
            )
            return
        try:
            snapshot = self.server.deck_service.snapshot(DEFAULT_CLIENT_ID)
            target = next(
                (
                    button
                    for button in snapshot.buttons
                    if button.session_id == session_id
                    and button.kind == "session"
                    and button.enabled
                ),
                None,
            )
            if target is None:
                self._send_json(
                    HTTPStatus.CONFLICT,
                    {"error": "session is not focusable in the current snapshot"},
                )
                return
            result = self.server.focus_controller.focus(session_id)
        except CursorProviderError as error:
            self._send_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": str(error), "retryable": True},
            )
            return
        if result.verdict == "FOCUS_VERIFIED":
            self.server.activity_store.acknowledge(
                session_id,
                time.time_ns() // 1_000_000,
            )
        status = (
            HTTPStatus.OK
            if result.verdict == "FOCUS_VERIFIED"
            else HTTPStatus.CONFLICT
        )
        self._send_json(status, result.to_dict())

    def _activate_intent(self) -> None:
        payload = self._read_json_payload(max_bytes=4_096)
        if payload is None:
            return
        self._activate_control(payload, DEFAULT_CLIENT_ID)

    def _activate_button(self) -> None:
        payload = self._read_json_payload(max_bytes=4_096)
        if payload is None:
            return
        client_id = payload.get("client_id")
        try:
            client_id = validate_client_id(client_id)
        except ValueError as error:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return
        button_id = payload.get("button_id")
        revision = payload.get("revision")
        if not isinstance(button_id, str) or not button_id:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "button_id must be a non-empty string"},
            )
            return
        if isinstance(revision, bool) or not isinstance(revision, int):
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "revision must be an integer"},
            )
            return
        try:
            snapshot = self.server.deck_service.snapshot(client_id)
            target = next(
                (
                    button
                    for button in snapshot.buttons
                    if button.id == button_id and button.enabled
                ),
                None,
            )
            if target is None:
                self._send_json(
                    HTTPStatus.CONFLICT,
                    {"error": "button is not actionable in the current snapshot"},
                )
                return
            if target.kind == "session" and target.session_id is not None:
                result = self.server.focus_controller.focus(target.session_id)
                if result.verdict == "FOCUS_VERIFIED":
                    self.server.activity_store.acknowledge(
                        target.session_id,
                        time.time_ns() // 1_000_000,
                    )
                status = (
                    HTTPStatus.OK
                    if result.verdict == "FOCUS_VERIFIED"
                    else HTTPStatus.CONFLICT
                )
                self._send_json(
                    status,
                    {
                        "accepted": status == HTTPStatus.OK,
                        "action": "focus_session",
                        "focus": result.to_dict(),
                    },
                )
                return
            if (
                target.kind not in {"control", "empty"}
                or target.action is None
            ):
                self._send_json(
                    HTTPStatus.CONFLICT,
                    {"error": "button is not actionable in the current snapshot"},
                )
                return
            self._dispatch_control(target.action, client_id)
        except (CursorProviderError, RuntimeError) as error:
            self._send_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": str(error), "retryable": True},
            )
            return

    def _activate_control(
        self,
        payload: dict[str, Any],
        client_id: str,
    ) -> None:
        button_id = payload.get("button_id")
        revision = payload.get("revision")
        if not isinstance(button_id, str) or not button_id:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "button_id must be a non-empty string"},
            )
            return
        if isinstance(revision, bool) or not isinstance(revision, int):
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "revision must be an integer"},
            )
            return
        try:
            snapshot = self.server.deck_service.snapshot(client_id)
            target = next(
                (
                    button
                    for button in snapshot.buttons
                    if button.id == button_id
                    and button.enabled
                    and button.action is not None
                    and button.kind in {"control", "empty"}
                ),
                None,
            )
            if target is None:
                self._send_json(
                    HTTPStatus.CONFLICT,
                    {"error": "button is not actionable in the current snapshot"},
                )
                return
            self._dispatch_control(target.action, client_id)
        except (CursorProviderError, RuntimeError) as error:
            self._send_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": str(error), "retryable": True},
            )
            return

    def _dispatch_control(self, action: str, client_id: str) -> None:
        try:
            if action == "refresh_sessions":
                updated = self.server.deck_service.refresh(client_id)
            elif action == "previous_page":
                updated = self.server.deck_service.previous_page(client_id)
            elif action == "next_page":
                updated = self.server.deck_service.next_page(client_id)
            elif action == "new_session":
                launch = self.server.launch_controller.open_new()
                status = (
                    HTTPStatus.OK
                    if launch.verdict == "NEW_AGENT_VIEW_REQUESTED"
                    else HTTPStatus.CONFLICT
                )
                self._send_json(
                    status,
                    {
                        "accepted": status == HTTPStatus.OK,
                        "action": action,
                        "launch": launch.to_dict(),
                    },
                )
                return
            else:
                self._send_json(
                    HTTPStatus.CONFLICT,
                    {"error": f"unsupported deck action: {action}"},
                )
                return
        except (CursorProviderError, RuntimeError) as error:
            self._send_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": str(error), "retryable": True},
            )
            return
        except ValueError as error:
            self._send_json(HTTPStatus.CONFLICT, {"error": str(error)})
            return
        self._send_json(
            HTTPStatus.OK,
            {
                "accepted": True,
                "action": action,
                "snapshot": updated.to_dict(),
            },
        )

    def _read_json_payload(
        self,
        *,
        max_bytes: int,
    ) -> dict[str, Any] | None:
        if self.headers.get_content_type() != "application/json":
            self._send_json(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                {"error": "payload must use application/json"},
            )
            return None
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            content_length = 0
        if not 0 < content_length <= max_bytes:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "invalid payload size"},
            )
            return None
        try:
            payload = json.loads(self.rfile.read(content_length))
            if not isinstance(payload, dict):
                raise ValueError("payload must be an object")
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as error:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": str(error)},
            )
            return None
        return payload

    def _receive_cursor_hook(self) -> None:
        payload = self._read_json_payload(max_bytes=65_536)
        if payload is None:
            return
        try:
            signal = self.server.activity_store.record(
                payload,
                time.time_ns() // 1_000_000,
            )
        except ValueError as error:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": str(error)},
            )
            return
        self._send_json(
            HTTPStatus.ACCEPTED,
            {"accepted": True, "session_id": signal.session_id},
        )

    def _serve_snapshot(self, query: str) -> None:
        parameters = parse_qs(query, keep_blank_values=True)
        client_ids = parameters.get("client_id", [DEFAULT_CLIENT_ID])
        if len(client_ids) != 1:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "client_id must be specified at most once"},
            )
            return
        try:
            client_id = validate_client_id(client_ids[0])
            snapshot = self.server.deck_service.snapshot(client_id)
        except ValueError as error:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return
        except CursorProviderError as error:
            self._send_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": str(error), "retryable": True},
            )
            return
        except RuntimeError as error:
            self._send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": str(error), "retryable": False},
            )
            return
        self._send_json(HTTPStatus.OK, snapshot.to_dict())

    def _serve_asset(self, request_path: str) -> None:
        if self.server.api_only:
            self._send_json(
                HTTPStatus.NOT_FOUND,
                {"error": "web assets are disabled in API-only mode"},
            )
            return
        relative_path = request_path.lstrip("/") or "index.html"
        assets = self.server.assets.resolve()
        candidate = (assets / relative_path).resolve()
        if not candidate.is_relative_to(assets):
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        if not candidate.is_file():
            candidate = assets / "index.html"
        if not candidate.is_file():
            self._send_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": f"web assets not found: {assets}"},
            )
            return
        payload = candidate.read_bytes()
        content_type = (
            mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        )
        cache_control = (
            "no-cache"
            if candidate.name == "index.html"
            else "public, max-age=31536000, immutable"
        )
        self._send_bytes(
            HTTPStatus.OK,
            payload,
            f"{content_type}; charset=utf-8"
            if content_type.startswith("text/")
            else content_type,
            cache_control=cache_control,
        )

    def _send_json(
        self,
        status: HTTPStatus,
        payload: dict[str, Any],
    ) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self._send_bytes(
            status,
            body,
            "application/json; charset=utf-8",
            cache_control="no-store",
        )

    def _send_bytes(
        self,
        status: HTTPStatus,
        payload: bytes,
        content_type: str,
        *,
        cache_control: str,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", cache_control)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "frame-ancestors 'none'",
        )
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format_string: str, *args: Any) -> None:
        print(f"HTTP {self.address_string()} {format_string % args}")


def serve(
    service: DeckService,
    activity_store: ActivityStore,
    focus_controller: CursorFocusController,
    launch_controller: CursorLaunchController,
    assets: Path,
    host: str,
    port: int,
    api_only: bool = False,
) -> None:
    """Serve until interrupted."""

    try:
        loopback = host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = False
    if not loopback:
        raise ValueError("deck server host must be a loopback address")

    server = DeckHTTPServer((host, port), DeckRequestHandler)
    server.deck_service = service
    server.activity_store = activity_store
    server.focus_controller = focus_controller
    server.launch_controller = launch_controller
    server.assets = assets
    server.api_only = api_only
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        server.server_close()
