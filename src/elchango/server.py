"""Loopback HTTP server for the local elChango web deck."""

from __future__ import annotations

import ipaddress
import json
import mimetypes
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from elchango.activity import ActivityStore
from elchango.deck import DEFAULT_CLIENT_ID, DeckService, validate_client_id
from elchango.providers.base import AgentProvider, ProviderError


class DeckHTTPServer(ThreadingHTTPServer):
    """Carry immutable application dependencies into request handlers."""

    daemon_threads = True
    deck_service: DeckService
    activity_store: ActivityStore
    hook_recorders: dict[str, Callable[[dict[str, Any], int], object]]
    providers: dict[str, AgentProvider]
    assets: Path
    api_only: bool = False


class DeckRequestHandler(BaseHTTPRequestHandler):
    """Serve versioned snapshots and the compiled Svelte application."""

    server: DeckHTTPServer

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            providers = self.server.providers
            self._send_json(
                HTTPStatus.OK,
                {
                    "status": "ok",
                    "focus_enabled": any(
                        "focus_session" in provider.capabilities
                        for provider in providers.values()
                    ),
                    "launch_enabled": any(
                        "new_session" in provider.capabilities
                        for provider in providers.values()
                    ),
                    "actions_enabled": False,
                    "providers": {
                        provider_id: sorted(provider.capabilities)
                        for provider_id, provider in providers.items()
                    },
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
        path = urlparse(self.path).path
        if path.startswith("/api/hooks/"):
            self._receive_provider_hook(path.removeprefix("/api/hooks/"))
            return
        if path == "/api/focus":
            self._focus_session()
            return
        if path == "/api/intent":
            self._activate_intent()
            return
        if path == "/api/activate":
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
                    if button.provider_id == "cursor"
                    and button.native_session_id == session_id
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
            provider = self.server.providers[target.provider_id]
            result = provider.focus(target.native_session_id)
        except (KeyError, ProviderError) as error:
            self._send_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": str(error), "retryable": True},
            )
            return
        status = HTTPStatus.OK if result.accepted else HTTPStatus.CONFLICT
        self._send_json(status, result.details)

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
            if (
                target.kind == "session"
                and target.provider_id is not None
                and target.native_session_id is not None
            ):
                provider = self.server.providers[target.provider_id]
                result = provider.focus(target.native_session_id)
                status = HTTPStatus.OK if result.accepted else HTTPStatus.CONFLICT
                self._send_json(
                    status,
                    {
                        "accepted": status == HTTPStatus.OK,
                        "action": "focus_session",
                        "focus": result.details,
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
            self._dispatch_control(
                target.action,
                client_id,
                target.provider_id,
            )
        except (KeyError, ProviderError, RuntimeError) as error:
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
            self._dispatch_control(
                target.action,
                client_id,
                target.provider_id,
            )
        except (KeyError, ProviderError, RuntimeError) as error:
            self._send_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": str(error), "retryable": True},
            )
            return

    def _dispatch_control(
        self,
        action: str,
        client_id: str,
        provider_id: str | None,
    ) -> None:
        try:
            if action == "refresh_sessions":
                updated = self.server.deck_service.refresh(client_id)
            elif action == "previous_page":
                updated = self.server.deck_service.previous_page(client_id)
            elif action == "next_page":
                updated = self.server.deck_service.next_page(client_id)
            elif action == "choose_new_provider":
                updated = self.server.deck_service.choose_new_provider(client_id)
            elif action == "cancel_new_session":
                updated = self.server.deck_service.cancel_new_session(client_id)
            elif action == "new_session":
                if provider_id is None:
                    raise ValueError("new session button has no provider target")
                launch = self.server.providers[provider_id].open_new()
                status = HTTPStatus.OK if launch.accepted else HTTPStatus.CONFLICT
                updated = (
                    self.server.deck_service.complete_new_session(client_id)
                    if launch.accepted
                    else None
                )
                self._send_json(
                    status,
                    {
                        "accepted": status == HTTPStatus.OK,
                        "action": action,
                        "launch": launch.details,
                        **(
                            {"snapshot": updated.to_dict()}
                            if updated is not None
                            else {}
                        ),
                    },
                )
                return
            else:
                self._send_json(
                    HTTPStatus.CONFLICT,
                    {"error": f"unsupported deck action: {action}"},
                )
                return
        except (KeyError, ProviderError, RuntimeError) as error:
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

    def _receive_provider_hook(self, provider_id: str) -> None:
        payload = self._read_json_payload(max_bytes=65_536)
        if payload is None:
            return
        recorders = getattr(
            self.server,
            "hook_recorders",
            {"cursor": self.server.activity_store.record},
        )
        recorder = recorders.get(provider_id)
        if recorder is None:
            self._send_json(
                HTTPStatus.NOT_FOUND,
                {"error": f"provider does not accept hooks: {provider_id}"},
            )
            return
        try:
            signal = recorder(
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
            {
                "accepted": True,
                "provider_id": provider_id,
                "session_id": getattr(signal, "session_id"),
            },
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
        except ProviderError as error:
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
    providers: dict[str, AgentProvider],
    assets: Path,
    host: str,
    port: int,
    api_only: bool = False,
    hook_recorders: dict[
        str,
        Callable[[dict[str, Any], int], object],
    ] | None = None,
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
    server.hook_recorders = hook_recorders or {
        "cursor": activity_store.record,
    }
    server.providers = providers
    server.assets = assets
    server.api_only = api_only
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        server.server_close()
