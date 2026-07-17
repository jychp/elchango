#!/usr/bin/env python3
"""POC M1.2: focus and verify a Cursor session from the local web deck.

Purpose
=======
Validate the first complete control loop:

    browser intent -> local server -> Cursor focus -> DB verification -> result

The deck displays the real sessions from POC M1.1. Clicking a card requests
focus for that exact composer ID. No prompt, tool, accept, reject, cancel,
launch, or other agent action is available.

Method
======
This POC reuses the observed session model from
``06_cursor_readonly_web_deck.py`` and the verified focus procedure from
``05_cursor_best_effort_focus.py``. For each focus request it:

1. accepts an exact composer ID from a same-origin browser;
2. validates that ID against Cursor's current local session records;
3. activates Cursor and refreshes the selected composer plus full MRU snapshot;
4. aborts if the snapshot changes before keyboard injection;
5. sends a bounded number of deliberate Control+Tab key events;
6. verifies the exact selected composer through the database;
7. reports the explicit focus verdict to the deck.

Safety
======
All Cursor database access remains ``mode=ro`` with ``query_only=ON``. The
server refuses non-loopback bind addresses. A random per-process token and
same-origin checks protect the focus endpoint from cross-site requests.
Concurrent focus attempts are rejected.

Focus is a UI side effect and requires macOS Accessibility permission. It can
briefly select the wrong session if Cursor's undocumented switcher behavior
changes. Post-action verification detects that condition, but cannot prevent
the brief incorrect focus. No subsequent agent action is dispatched.

Examples
========
    python scripts/poc/07_cursor_web_deck_focus.py
    python scripts/poc/07_cursor_web_deck_focus.py --open
    python scripts/poc/07_cursor_web_deck_focus.py --port 8878

Interpretation
==============
``FOCUS_VERIFIED`` proves that a browser intent can target a real native Cursor
session and receive exact DB-backed confirmation. Other verdicts are safe
failures for this POC because they never trigger an agent action.

Limitations
===========
The focus mechanism depends on undocumented recency fields and Cursor's
Control+Tab switcher. It is best-effort, macOS-only, and requires Accessibility
permission. Revalidate after Cursor upgrades and against larger mixed
local/cloud session sets.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import secrets
import sqlite3
import sys
import threading
import webbrowser
from dataclasses import asdict
from http import HTTPStatus
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any
from urllib.parse import urlparse


SCRIPT_DIRECTORY = Path(__file__).resolve().parent
COMPOSER_ID_PATTERN = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)


def load_sibling_module(filename: str, module_name: str) -> ModuleType:
    path = SCRIPT_DIRECTORY / filename
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load POC dependency: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


DECK = load_sibling_module(
    "06_cursor_readonly_web_deck.py",
    "elchango_poc_06_deck",
)
FOCUS = load_sibling_module(
    "05_cursor_best_effort_focus.py",
    "elchango_poc_05_focus",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Serve a local Cursor deck with verified focus controls.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "The only side effect is best-effort Cursor session focus.\n"
            "No agent-content action endpoint exists."
        ),
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=DECK.DEFAULT_DATABASE,
        help=f"Cursor state.vscdb path (default: {DECK.DEFAULT_DATABASE})",
    )
    parser.add_argument(
        "--workspace-storage",
        type=Path,
        default=DECK.DEFAULT_WORKSPACE_STORAGE,
        help="Cursor workspaceStorage directory.",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Loopback HTTP bind host (default: 127.0.0.1).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8766,
        help="HTTP bind port (default: 8766).",
    )
    parser.add_argument(
        "--poll-ms",
        type=int,
        default=750,
        help="Browser polling interval in milliseconds (default: 750).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="Focus verification timeout in seconds (default: 5).",
    )
    parser.add_argument(
        "--open",
        action="store_true",
        help="Open the deck in the default browser after startup.",
    )
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost"}:
        parser.error("--host must be 127.0.0.1 or localhost")
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    if args.poll_ms < 100:
        parser.error("--poll-ms must be at least 100")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    return args


def focus_session(
    database: Path,
    workspace_storage: Path,
    composer_id: str,
    timeout: float,
) -> dict[str, Any]:
    args = SimpleNamespace(
        target=composer_id,
        database=database,
        workspace_storage=workspace_storage,
        execute=True,
        timeout=timeout,
        interval=0.1,
        json=True,
    )
    with FOCUS.connect_read_only(database) as connection:
        FOCUS.validate_schema(connection)
        result = FOCUS.inspect_or_focus(connection, args)
    payload = asdict(result)
    payload["safe_for_agent_action"] = result.verdict == "FOCUS_VERIFIED"
    return payload


FOCUS_STYLE = r"""
    .session {
      width: 100%;
      color: inherit;
      font: inherit;
      text-align: left;
      cursor: pointer;
      appearance: none;
    }

    .session:hover {
      border-color: var(--blue);
      transform: translateY(-1px);
    }

    .session:focus-visible {
      outline: 4px solid var(--blue-soft);
      outline-offset: 2px;
    }

    .session[disabled] {
      cursor: wait;
      opacity: .62;
    }

    .feedback {
      display: none;
      align-items: flex-start;
      justify-content: space-between;
      gap: 18px;
      margin: 4px 0 16px;
      border: 1px solid var(--line);
      border-left: 7px solid var(--blue);
      border-radius: 12px;
      background: var(--panel);
      padding: 14px 16px;
    }

    .feedback.visible { display: flex; }
    .feedback.success { border-left-color: var(--green); }
    .feedback.failure { border-left-color: var(--red); }

    .feedback strong {
      display: block;
      margin-bottom: 4px;
      font-size: 14px;
    }

    .feedback span {
      color: var(--muted);
      font-size: 12px;
    }

    .feedback code {
      color: var(--muted);
      font: 600 10px/1.4 ui-monospace, "SFMono-Regular", Menlo, monospace;
    }
"""


FOCUS_SCRIPT = r"""
    const actionToken = "__ACTION_TOKEN__";
    const feedback = document.querySelector("#feedback");
    let focusPending = false;

    function showFeedback(kind, title, detail, verdict = "") {
      feedback.className = `feedback visible ${kind}`;
      feedback.innerHTML = `
        <div>
          <strong>${escapeHtml(title)}</strong>
          <span>${escapeHtml(detail)}</span>
        </div>
        <code>${escapeHtml(verdict)}</code>`;
    }

    deck.addEventListener("click", async (event) => {
      const card = event.target.closest("[data-composer-id]");
      if (!card || focusPending) return;

      const composerId = card.dataset.composerId;
      const title = card.dataset.title;
      focusPending = true;
      document.querySelectorAll(".session").forEach((item) => {
        item.disabled = true;
      });
      showFeedback("pending", "Focusing Cursor…", title);

      try {
        const response = await fetch("/api/focus", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-ElChango-Token": actionToken,
          },
          body: JSON.stringify({ composer_id: composerId }),
        });
        const payload = await response.json();
        if (!response.ok) {
          throw new Error(payload.error || `HTTP ${response.status}`);
        }
        const success = payload.verdict === "FOCUS_VERIFIED";
        showFeedback(
          success ? "success" : "failure",
          success ? "Exact session verified" : "Focus not verified",
          (payload.reasons || []).join(" "),
          payload.verdict
        );
        await refresh();
      } catch (error) {
        showFeedback("failure", "Focus request failed", error.message);
      } finally {
        focusPending = false;
        document.querySelectorAll(".session").forEach((item) => {
          item.disabled = false;
        });
      }
    });
"""


def build_focus_html(poll_ms: int, action_token: str) -> bytes:
    html = DECK.HTML_TEMPLATE
    html = html.replace(
        "<title>elChango · Cursor field board</title>",
        "<title>elChango · verified Cursor focus</title>",
    )
    html = html.replace("</style>", f"{FOCUS_STYLE}\n  </style>")
    html = html.replace(
        "elChango / Cursor field board",
        "elChango / verified focus board",
    )
    html = html.replace(
        "Native agents.<br>One quiet control surface.",
        "Choose a session.<br>Verify the landing.",
    )
    html = html.replace(
        '<section class="deck" id="deck" aria-live="polite"></section>',
        (
            '<section class="feedback" id="feedback" aria-live="polite"></section>'
            '\n    <section class="deck" id="deck" aria-live="polite"></section>'
        ),
    )
    html = html.replace(
        '<article class="session state-${state}${session.selected ? " selected" : ""}">',
        (
            '<button type="button" '
            'class="session state-${state}${session.selected ? " selected" : ""}" '
            'data-composer-id="${escapeHtml(session.composer_id)}" '
            'data-title="${escapeHtml(session.title)}">'
        ),
    )
    html = html.replace("</article>`;", "</button>`;")
    html = html.replace(
        "Read-only POC",
        "DB read-only · focus only",
    )
    html = html.replace(
        "No card can act\n        on a session.",
        "Cards can request verified focus. No agent-content action is available.",
    )
    html = html.replace(
        "    refreshButton.addEventListener(\"click\", refresh);",
        (
            FOCUS_SCRIPT.replace("__ACTION_TOKEN__", action_token)
            + '\n    refreshButton.addEventListener("click", refresh);'
        ),
    )
    return html.replace("__POLL_MS__", str(poll_ms)).encode("utf-8")


class FocusDeckServer(DECK.DeckServer):
    """Deck server with one protected focus endpoint."""

    action_token: str
    focus_timeout: float
    focus_lock: threading.Lock
    allowed_origins: frozenset[str]


class FocusDeckHandler(DECK.DeckHandler):
    """Serve snapshots and accept protected exact-focus intents."""

    server: FocusDeckServer

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/api/focus":
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        if not self.request_is_authorized():
            self.send_json(HTTPStatus.FORBIDDEN, {"error": "focus request rejected"})
            return
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0]
        if content_type != "application/json":
            self.send_json(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                {"error": "application/json is required"},
            )
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if not 1 <= length <= 4096:
            self.send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "invalid request size"},
            )
            return
        try:
            payload = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid JSON"})
            return
        composer_id = (
            payload.get("composer_id") if isinstance(payload, dict) else None
        )
        if not isinstance(composer_id, str) or not COMPOSER_ID_PATTERN.fullmatch(
            composer_id
        ):
            self.send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "invalid composer_id"},
            )
            return
        if not self.server.focus_lock.acquire(blocking=False):
            self.send_json(
                HTTPStatus.CONFLICT,
                {"error": "another focus request is still running"},
            )
            return
        try:
            result = focus_session(
                self.server.database,
                self.server.workspace_storage,
                composer_id,
                self.server.focus_timeout,
            )
        except (
            FileNotFoundError,
            RuntimeError,
            sqlite3.Error,
            OSError,
        ) as error:
            self.send_json(
                HTTPStatus.UNPROCESSABLE_ENTITY,
                {"error": str(error)},
            )
            return
        finally:
            self.server.focus_lock.release()
        self.send_json(HTTPStatus.OK, result)

    def request_is_authorized(self) -> bool:
        token = self.headers.get("X-ElChango-Token", "")
        if not secrets.compare_digest(token, self.server.action_token):
            return False
        origin = self.headers.get("Origin")
        return origin is None or origin in self.server.allowed_origins

    def send_json(
        self,
        status: HTTPStatus,
        payload: dict[str, Any],
    ) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_bytes(
            status,
            body,
            "application/json; charset=utf-8",
        )


def serve(args: argparse.Namespace) -> int:
    DECK.build_snapshot(args.database, args.workspace_storage)
    action_token = secrets.token_urlsafe(32)
    server = FocusDeckServer((args.host, args.port), FocusDeckHandler)
    server.database = args.database
    server.workspace_storage = args.workspace_storage
    server.action_token = action_token
    server.focus_timeout = args.timeout
    server.focus_lock = threading.Lock()
    server.allowed_origins = frozenset(
        {
            f"http://{args.host}:{args.port}",
            f"http://127.0.0.1:{args.port}",
            f"http://localhost:{args.port}",
        }
    )
    server.page = build_focus_html(args.poll_ms, action_token)
    url = f"http://{args.host}:{args.port}/"
    print("Cursor verified-focus web deck POC M1.2")
    print(f"URL: {url}")
    print(f"Database: {args.database}")
    print("Safety: DB read-only, loopback-only, focus-only, exact verification")
    print("Press Ctrl-C to stop.")
    if args.open:
        threading.Timer(0.2, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        print("\nServer stopped.")
    finally:
        server.server_close()
    return 0


def main() -> int:
    args = parse_args()
    try:
        return serve(args)
    except (FileNotFoundError, RuntimeError, sqlite3.Error, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
