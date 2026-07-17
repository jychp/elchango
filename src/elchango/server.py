"""Loopback HTTP server for the local elChango web deck."""

from __future__ import annotations

import json
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from elchango.deck import DeckService
from elchango.providers.cursor import CursorProviderError


class DeckHTTPServer(ThreadingHTTPServer):
    """Carry immutable application dependencies into request handlers."""

    daemon_threads = True
    deck_service: DeckService
    assets: Path


class DeckRequestHandler(BaseHTTPRequestHandler):
    """Serve versioned snapshots and the compiled Svelte application."""

    server: DeckHTTPServer

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            self._send_json(
                HTTPStatus.OK,
                {"status": "ok", "actions_enabled": False},
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
        self._send_json(
            HTTPStatus.METHOD_NOT_ALLOWED,
            {
                "error": "actions are not enabled in the foundation slice",
            },
        )

    def _serve_snapshot(self, query: str) -> None:
        values = parse_qs(query)
        try:
            page = int(values.get("page", ["0"])[0])
        except ValueError:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "page must be an integer"},
            )
            return
        try:
            snapshot = self.server.deck_service.snapshot(page)
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
    assets: Path,
    host: str,
    port: int,
) -> None:
    """Serve until interrupted."""

    server = DeckHTTPServer((host, port), DeckRequestHandler)
    server.deck_service = service
    server.assets = assets
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        server.server_close()
