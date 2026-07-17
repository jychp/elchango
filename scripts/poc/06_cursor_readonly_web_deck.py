#!/usr/bin/env python3
"""POC M1.1: serve a read-only web deck backed by live Cursor sessions.

Purpose
=======
Build the first real vertical slice after Cursor feasibility reconnaissance.
The POC serves a responsive local web deck that displays:

1. candidate local Cursor sessions;
2. workspace association;
3. the exact selected composer;
4. conservative execution state derived from DB-only signals;
5. live updates through short-interval HTTP polling.

This is deliberately not the final core, bridge, provider, or surface contract.
It tests the user-visible loop before durable abstractions are introduced.

Method
======
The POC reads Cursor's live WAL-backed ``state.vscdb`` and workspace metadata.
It serves one embedded HTML document plus ``/api/sessions`` from Python's
standard-library HTTP server. The browser polls the API and renders the latest
snapshot. No build step or package installation is required.

Safety
======
The database is opened with SQLite ``mode=ro`` and ``PRAGMA query_only=ON``.
The server binds to ``127.0.0.1`` by default. It exposes only redacted metadata:
IDs, titles, workspace paths, timestamps, type codes, and normalized states.
It never returns prompts, responses, tool arguments, tool output, file content,
or secrets.

The deck is read-only. It cannot focus, launch, stop, accept, reject, or send
anything to an agent. ``--open`` is the only optional UI side effect.

Examples
========
    python scripts/poc/06_cursor_readonly_web_deck.py
    python scripts/poc/06_cursor_readonly_web_deck.py --open
    python scripts/poc/06_cursor_readonly_web_deck.py --port 8877
    python scripts/poc/06_cursor_readonly_web_deck.py --json

Interpretation
==============
A working deck validates a thin real-session slice: Cursor DB to normalized
snapshot to browser rendering. It does not validate a final WebSocket bridge,
multi-surface fan-out, remote access, actions, or launch.

Execution states remain conservative:

- ``running`` requires an observed loading/running tool or generation signal;
- ``waiting`` requires an observed blocking pending-action signal;
- ``error`` requires an observed error/failure status;
- ``done`` is persisted completion evidence, not proof of current activity;
- ``idle`` includes sessions whose last persisted turn is complete or aborted;
- ``unknown`` means available fields do not justify a stronger conclusion.

Limitations
===========
Cursor's tables and JSON fields are undocumented. Intermediate bubble statuses
can be revised when Cursor persists a completed turn. Session IDs still require
restart and upgrade stability testing. Revalidate after Cursor upgrades.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import threading
import time
import webbrowser
from dataclasses import asdict, dataclass
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse


DEFAULT_CURSOR_ROOT = Path.home() / "Library/Application Support/Cursor/User"
DEFAULT_DATABASE = DEFAULT_CURSOR_ROOT / "globalStorage/state.vscdb"
DEFAULT_WORKSPACE_STORAGE = DEFAULT_CURSOR_ROOT / "workspaceStorage"
SELECTED_AGENT_KEY = "cursor/glass.selectedAgent"
COMPOSER_KEY_PREFIX = "composerData:"
BUBBLE_KEY_PREFIX = "bubbleId:"
REQUIRED_TABLES = {"ItemTable", "composerHeaders", "cursorDiskKV"}


@dataclass(frozen=True)
class DeckSession:
    """Redacted session model returned to the browser."""

    composer_id: str
    title: str
    workspace_id: str
    workspace_path: str | None
    selected: bool
    state: str
    state_detail: str
    state_confidence: str
    updated_at_ms: int


@dataclass(frozen=True)
class DeckSnapshot:
    """One complete redacted deck update."""

    observed_at_ms: int
    selected_composer_id: str | None
    sessions: tuple[DeckSession, ...]
    source: str
    read_only: bool
    warnings: tuple[str, ...]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Serve a read-only web deck of live Cursor sessions.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "The server binds to loopback and exposes redacted metadata only.\n"
            "It has no focus, launch, prompt, or agent-action endpoint."
        ),
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=DEFAULT_DATABASE,
        help=f"Cursor state.vscdb path (default: {DEFAULT_DATABASE})",
    )
    parser.add_argument(
        "--workspace-storage",
        type=Path,
        default=DEFAULT_WORKSPACE_STORAGE,
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
        default=8765,
        help="HTTP bind port (default: 8765).",
    )
    parser.add_argument(
        "--poll-ms",
        type=int,
        default=750,
        help="Browser polling interval in milliseconds (default: 750).",
    )
    parser.add_argument(
        "--open",
        action="store_true",
        help="Open the deck in the default browser after startup.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print one snapshot as JSON instead of starting a server.",
    )
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost"}:
        parser.error("--host must be 127.0.0.1 or localhost")
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    if args.poll_ms < 100:
        parser.error("--poll-ms must be at least 100")
    return args


def connect_read_only(database: Path) -> sqlite3.Connection:
    if not database.is_file():
        raise FileNotFoundError(f"Cursor database not found: {database}")
    connection = sqlite3.connect(
        f"{database.resolve().as_uri()}?mode=ro",
        uri=True,
        timeout=2,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def validate_schema(connection: sqlite3.Connection) -> None:
    tables = {
        row["name"]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    missing = REQUIRED_TABLES - tables
    if missing:
        raise RuntimeError(
            f"Unsupported Cursor schema; missing tables: {sorted(missing)}"
        )


def decode_text(value: Any) -> str | None:
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return None
    return value if isinstance(value, str) else None


def parse_object(value: Any) -> dict[str, Any]:
    text = decode_text(value)
    if text is None:
        return {}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def read_selected_id(connection: sqlite3.Connection) -> str | None:
    row = connection.execute(
        "SELECT value FROM ItemTable WHERE key = ?",
        (SELECTED_AGENT_KEY,),
    ).fetchone()
    if row is None:
        return None
    value = decode_text(row["value"])
    return value.strip() if value and value.strip() else None


def file_uri_to_path(uri: str) -> str | None:
    parsed = urlparse(uri)
    return unquote(parsed.path) if parsed.scheme == "file" else None


def load_workspace_paths(workspace_storage: Path) -> dict[str, str]:
    paths: dict[str, str] = {}
    if not workspace_storage.is_dir():
        return paths
    for workspace_file in workspace_storage.glob("*/workspace.json"):
        try:
            payload = json.loads(workspace_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        folder = payload.get("folder")
        if isinstance(folder, str):
            path = file_uri_to_path(folder)
            if path:
                paths[workspace_file.parent.name] = path
    return paths


def embedded_workspace_path(header: dict[str, Any]) -> str | None:
    agent_location = header.get("agentLocation")
    environment = (
        agent_location.get("environment")
        if isinstance(agent_location, dict)
        else None
    )
    workspace_identifier = header.get("workspaceIdentifier")
    candidates = [
        environment.get("uri") if isinstance(environment, dict) else None,
        (
            workspace_identifier.get("uri")
            if isinstance(workspace_identifier, dict)
            else None
        ),
    ]
    for uri in candidates:
        if not isinstance(uri, dict):
            continue
        path = uri.get("fsPath") or uri.get("path")
        if isinstance(path, str) and path:
            return path
        external = uri.get("external")
        if isinstance(external, str):
            path = file_uri_to_path(external)
            if path:
                return path
    return None


def read_memberships(connection: sqlite3.Connection) -> dict[str, Any]:
    row = connection.execute(
        "SELECT value FROM ItemTable WHERE key = ?",
        ("glass.localAgentProjectMembership.v1",),
    ).fetchone()
    return parse_object(row["value"]) if row is not None else {}


def read_composer_data(
    connection: sqlite3.Connection,
    composer_id: str,
) -> dict[str, Any]:
    row = connection.execute(
        "SELECT value FROM cursorDiskKV WHERE key = ?",
        (f"{COMPOSER_KEY_PREFIX}{composer_id}",),
    ).fetchone()
    return parse_object(row["value"]) if row is not None else {}


def read_bubble(
    connection: sqlite3.Connection,
    composer_id: str,
    bubble_id: str,
) -> dict[str, Any]:
    row = connection.execute(
        "SELECT value FROM cursorDiskKV WHERE key = ?",
        (f"{BUBBLE_KEY_PREFIX}{composer_id}:{bubble_id}",),
    ).fetchone()
    return parse_object(row["value"]) if row is not None else {}


def latest_tool_status(
    connection: sqlite3.Connection,
    composer_id: str,
    data: dict[str, Any],
) -> tuple[str | None, str | None]:
    headers = data.get("fullConversationHeadersOnly")
    if not isinstance(headers, list):
        return None, None
    for header in reversed(headers[-20:]):
        if not isinstance(header, dict):
            continue
        bubble_id = header.get("bubbleId")
        if not isinstance(bubble_id, str):
            continue
        bubble = read_bubble(connection, composer_id, bubble_id)
        tool_data = bubble.get("toolFormerData")
        if not isinstance(tool_data, dict):
            continue
        additional = tool_data.get("additionalData")
        result_status = (
            additional.get("status") if isinstance(additional, dict) else None
        )
        return (
            string_or_none(tool_data.get("status")),
            string_or_none(result_status),
        )
    return None, None


def infer_state(
    connection: sqlite3.Connection,
    composer_id: str,
) -> tuple[str, str, str]:
    data = read_composer_data(connection, composer_id)
    if not data:
        return "unknown", "composer data unavailable", "unknown"

    tool_status, result_status = latest_tool_status(
        connection,
        composer_id,
        data,
    )
    normalized_tool = tool_status.lower() if tool_status else None
    normalized_result = result_status.lower() if result_status else None

    if normalized_result in {"error", "failed", "failure"}:
        return "error", f"tool result {normalized_result}", "observed"
    if normalized_tool in {"error", "failed", "failure"}:
        return "error", f"tool {normalized_tool}", "observed"
    if bool(data.get("hasBlockingPendingActions", False)):
        return "waiting", "blocking action pending", "candidate"
    if normalized_tool in {"loading", "running", "pending", "in_progress"}:
        return "running", f"tool {normalized_tool}", "candidate"
    if data.get("generatingBubbleIds") or bool(
        data.get("isContinuationInProgress", False)
    ):
        return "running", "generation signal present", "candidate"
    if normalized_tool == "completed" and normalized_result == "success":
        return "done", "tool completed successfully", "persisted"
    if normalized_tool == "completed":
        return "done", "tool completed", "persisted"

    raw_status = string_or_none(data.get("status"))
    if raw_status in {"error", "failed"}:
        return "error", f"composer {raw_status}", "persisted"
    if raw_status == "completed":
        return "done", "last turn completed", "persisted"
    if raw_status == "aborted":
        return "idle", "last turn aborted", "persisted"
    if raw_status in {"generating", "running", "pending"}:
        return "running", f"composer {raw_status}", "candidate"
    return "unknown", "no decisive state signal", "unknown"


def string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def build_snapshot(
    database: Path,
    workspace_storage: Path,
) -> DeckSnapshot:
    with connect_read_only(database) as connection:
        validate_schema(connection)
        selected_id = read_selected_id(connection)
        workspace_paths = load_workspace_paths(workspace_storage)
        memberships = read_memberships(connection)
        sessions: list[DeckSession] = []

        rows = connection.execute(
            """
            SELECT composerId, workspaceId, lastUpdatedAt, recency,
                   isArchived, isSubagent, value
            FROM composerHeaders
            """
        )
        for row in rows:
            composer_id = str(row["composerId"])
            header = parse_object(row["value"])
            if memberships and composer_id not in memberships:
                continue
            if bool(row["isArchived"]) or bool(row["isSubagent"]):
                continue
            if bool(header.get("isDraft", False)):
                continue
            if bool(header.get("isEphemeral", False)):
                continue
            updated_at = row["recency"]
            if not isinstance(updated_at, int):
                updated_at = row["lastUpdatedAt"]
            if not isinstance(updated_at, int):
                continue

            workspace_id = str(row["workspaceId"] or "")
            workspace_path = (
                embedded_workspace_path(header)
                or workspace_paths.get(workspace_id)
            )
            title = string_or_none(header.get("name")) or "Untitled session"
            state, state_detail, confidence = infer_state(
                connection,
                composer_id,
            )
            sessions.append(
                DeckSession(
                    composer_id=composer_id,
                    title=title,
                    workspace_id=workspace_id,
                    workspace_path=workspace_path,
                    selected=composer_id == selected_id,
                    state=state,
                    state_detail=state_detail,
                    state_confidence=confidence,
                    updated_at_ms=updated_at,
                )
            )

        sessions.sort(
            key=lambda session: (
                not session.selected,
                -session.updated_at_ms,
            )
        )
        warnings = (
            "Execution states are conservative DB-only interpretations.",
            "Cursor storage fields are undocumented and version-fragile.",
            "This deck has no agent action endpoints.",
        )
        return DeckSnapshot(
            observed_at_ms=time.time_ns() // 1_000_000,
            selected_composer_id=selected_id,
            sessions=tuple(sessions),
            source=str(database),
            read_only=True,
            warnings=warnings,
        )


def snapshot_payload(snapshot: DeckSnapshot) -> dict[str, Any]:
    return asdict(snapshot)


HTML_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>elChango · Cursor field board</title>
  <style>
    :root {
      color-scheme: light;
      --paper: #edf0f4;
      --panel: #f8fafc;
      --ink: #172033;
      --muted: #697386;
      --line: #cbd2dc;
      --blue: #2657d6;
      --blue-soft: #dbe5ff;
      --green: #16765b;
      --amber: #a05a00;
      --red: #b62f42;
      --slate: #667085;
      --unknown: #8b7ca8;
      --radius: 18px;
      font-family: "Avenir Next", Avenir, "Segoe UI", sans-serif;
    }

    * { box-sizing: border-box; }

    body {
      margin: 0;
      min-height: 100vh;
      background: var(--paper);
      color: var(--ink);
    }

    button { font: inherit; }

    .shell {
      width: min(1180px, calc(100% - 32px));
      margin: 0 auto;
      padding: 28px 0 48px;
    }

    .masthead {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 24px;
      align-items: end;
      padding: 4px 2px 22px;
      border-bottom: 1px solid var(--line);
    }

    .eyebrow {
      margin: 0 0 8px;
      color: var(--blue);
      font: 700 11px/1.2 ui-monospace, "SFMono-Regular", Menlo, monospace;
      letter-spacing: .16em;
      text-transform: uppercase;
    }

    h1 {
      margin: 0;
      max-width: 760px;
      font-size: clamp(30px, 5vw, 58px);
      font-weight: 650;
      letter-spacing: -.045em;
      line-height: .98;
    }

    .status-cluster {
      display: grid;
      justify-items: end;
      gap: 7px;
      font: 600 12px/1.3 ui-monospace, "SFMono-Regular", Menlo, monospace;
      color: var(--muted);
    }

    .live {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      color: var(--green);
    }

    .live::before {
      content: "";
      width: 9px;
      height: 9px;
      border-radius: 50%;
      background: currentColor;
    }

    .toolbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 18px 2px 14px;
    }

    .summary {
      color: var(--muted);
      font-size: 14px;
    }

    .refresh {
      border: 1px solid var(--line);
      border-radius: 999px;
      background: transparent;
      color: var(--ink);
      padding: 8px 13px;
      cursor: pointer;
    }

    .refresh:hover { background: var(--panel); }
    .refresh:focus-visible { outline: 3px solid var(--blue-soft); }

    .deck {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(270px, 1fr));
      gap: 14px;
    }

    .session {
      position: relative;
      min-height: 216px;
      overflow: hidden;
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: var(--panel);
      padding: 20px 20px 18px 26px;
    }

    .session::before {
      content: "";
      position: absolute;
      inset: 0 auto 0 0;
      width: 7px;
      background: var(--state-color);
    }

    .session.selected {
      border-color: var(--blue);
      background: #f4f7ff;
    }

    .session.selected::after {
      content: "SELECTED";
      position: absolute;
      top: 14px;
      right: -30px;
      width: 110px;
      padding: 5px 0;
      transform: rotate(38deg);
      background: var(--blue);
      color: white;
      text-align: center;
      font: 700 9px/1 ui-monospace, "SFMono-Regular", Menlo, monospace;
      letter-spacing: .1em;
    }

    .state-running { --state-color: var(--amber); }
    .state-waiting { --state-color: var(--blue); }
    .state-done { --state-color: var(--green); }
    .state-error { --state-color: var(--red); }
    .state-idle { --state-color: var(--slate); }
    .state-unknown { --state-color: var(--unknown); }

    .state-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 28px;
    }

    .state-label {
      color: var(--state-color);
      font: 750 11px/1 ui-monospace, "SFMono-Regular", Menlo, monospace;
      letter-spacing: .12em;
      text-transform: uppercase;
    }

    .confidence {
      color: var(--muted);
      font: 600 10px/1 ui-monospace, "SFMono-Regular", Menlo, monospace;
      text-transform: uppercase;
    }

    .session h2 {
      margin: 0 20px 8px 0;
      font-size: 21px;
      line-height: 1.12;
      letter-spacing: -.02em;
    }

    .workspace {
      min-height: 38px;
      margin: 0;
      color: var(--muted);
      font: 500 12px/1.45 ui-monospace, "SFMono-Regular", Menlo, monospace;
      overflow-wrap: anywhere;
    }

    .session-footer {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      margin-top: 24px;
      padding-top: 12px;
      border-top: 1px solid var(--line);
      color: var(--muted);
      font: 500 11px/1.3 ui-monospace, "SFMono-Regular", Menlo, monospace;
    }

    .empty, .error {
      grid-column: 1 / -1;
      border: 1px solid var(--line);
      border-radius: var(--radius);
      padding: 32px;
      background: var(--panel);
    }

    .error { border-color: var(--red); color: var(--red); }

    .footnote {
      display: grid;
      grid-template-columns: auto minmax(0, 1fr);
      gap: 16px;
      margin-top: 22px;
      padding-top: 18px;
      border-top: 1px solid var(--line);
      color: var(--muted);
      font-size: 12px;
      line-height: 1.5;
    }

    .readonly {
      color: var(--blue);
      font: 700 10px/1.4 ui-monospace, "SFMono-Regular", Menlo, monospace;
      letter-spacing: .1em;
      text-transform: uppercase;
    }

    @media (max-width: 620px) {
      .shell { width: min(100% - 20px, 1180px); padding-top: 18px; }
      .masthead { grid-template-columns: 1fr; }
      .status-cluster { justify-items: start; }
      .toolbar { align-items: flex-start; }
      .deck { grid-template-columns: 1fr; }
      .footnote { grid-template-columns: 1fr; }
    }

    @media (prefers-reduced-motion: no-preference) {
      .live::before { animation: pulse 1.8s ease-in-out infinite; }
      @keyframes pulse { 50% { opacity: .35; } }
    }
  </style>
</head>
<body>
  <main class="shell">
    <header class="masthead">
      <div>
        <p class="eyebrow">elChango / Cursor field board</p>
        <h1>Native agents.<br>One quiet control surface.</h1>
      </div>
      <div class="status-cluster">
        <span class="live" id="connection">live DB feed</span>
        <span id="observed">waiting for snapshot</span>
      </div>
    </header>

    <section class="toolbar" aria-label="Deck controls">
      <div class="summary" id="summary">Loading Cursor sessions…</div>
      <button class="refresh" id="refresh" type="button">Refresh now</button>
    </section>

    <section class="deck" id="deck" aria-live="polite"></section>

    <footer class="footnote">
      <span class="readonly">Read-only POC</span>
      <span>
        Metadata comes from Cursor’s local state database. Status labels are
        conservative interpretations of undocumented fields. No card can act
        on a session.
      </span>
    </footer>
  </main>

  <script>
    const pollMs = __POLL_MS__;
    const deck = document.querySelector("#deck");
    const summary = document.querySelector("#summary");
    const observed = document.querySelector("#observed");
    const connection = document.querySelector("#connection");
    const refreshButton = document.querySelector("#refresh");

    function escapeHtml(value) {
      return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
    }

    function shortId(value) {
      return value ? `${value.slice(0, 8)}…` : "unknown";
    }

    function relativeTime(timestamp) {
      const seconds = Math.max(0, Math.round((Date.now() - timestamp) / 1000));
      if (seconds < 5) return "now";
      if (seconds < 60) return `${seconds}s ago`;
      const minutes = Math.round(seconds / 60);
      if (minutes < 60) return `${minutes}m ago`;
      const hours = Math.round(minutes / 60);
      if (hours < 24) return `${hours}h ago`;
      return `${Math.round(hours / 24)}d ago`;
    }

    function render(snapshot) {
      const sessions = snapshot.sessions || [];
      const selected = sessions.find((session) => session.selected);
      summary.textContent = `${sessions.length} live candidates · ${
        selected ? `selected: ${selected.title}` : "no selected candidate"
      }`;
      observed.textContent = new Date(snapshot.observed_at_ms).toLocaleTimeString();
      connection.textContent = "live DB feed";
      connection.style.color = "";

      if (!sessions.length) {
        deck.innerHTML = `
          <div class="empty">
            No candidate sessions are available. Open a local Cursor agent,
            then refresh this deck.
          </div>`;
        return;
      }

      deck.innerHTML = sessions.map((session) => {
        const state = escapeHtml(session.state);
        const workspace = escapeHtml(
          session.workspace_path || `workspace:${session.workspace_id}`
        );
        return `
          <article class="session state-${state}${session.selected ? " selected" : ""}">
            <div class="state-row">
              <span class="state-label">${state}</span>
              <span class="confidence">${escapeHtml(session.state_confidence)}</span>
            </div>
            <h2>${escapeHtml(session.title)}</h2>
            <p class="workspace">${workspace}</p>
            <div class="session-footer">
              <span>${shortId(session.composer_id)}</span>
              <span title="${escapeHtml(session.state_detail)}">
                ${relativeTime(session.updated_at_ms)}
              </span>
            </div>
          </article>`;
      }).join("");
    }

    async function refresh() {
      try {
        const response = await fetch("/api/sessions", { cache: "no-store" });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        render(await response.json());
      } catch (error) {
        connection.textContent = "feed unavailable";
        connection.style.color = "var(--red)";
        deck.innerHTML = `
          <div class="error">
            Could not read Cursor state. ${escapeHtml(error.message)}
          </div>`;
      }
    }

    refreshButton.addEventListener("click", refresh);
    refresh();
    setInterval(refresh, pollMs);
  </script>
</body>
</html>
"""


def build_html(poll_ms: int) -> bytes:
    return HTML_TEMPLATE.replace("__POLL_MS__", str(poll_ms)).encode("utf-8")


class DeckServer(ThreadingHTTPServer):
    """HTTP server carrying immutable POC configuration."""

    database: Path
    workspace_storage: Path
    page: bytes


class DeckHandler(BaseHTTPRequestHandler):
    """Serve the embedded deck and redacted session snapshots."""

    server: DeckServer

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            self.send_bytes(
                HTTPStatus.OK,
                self.server.page,
                "text/html; charset=utf-8",
            )
            return
        if path == "/api/sessions":
            try:
                snapshot = build_snapshot(
                    self.server.database,
                    self.server.workspace_storage,
                )
                payload = json.dumps(
                    snapshot_payload(snapshot),
                    separators=(",", ":"),
                ).encode("utf-8")
            except (FileNotFoundError, RuntimeError, sqlite3.Error) as error:
                payload = json.dumps(
                    {"error": str(error)},
                    separators=(",", ":"),
                ).encode("utf-8")
                self.send_bytes(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    payload,
                    "application/json; charset=utf-8",
                )
                return
            self.send_bytes(
                HTTPStatus.OK,
                payload,
                "application/json; charset=utf-8",
            )
            return
        if path == "/api/health":
            payload = b'{"status":"ok","read_only":true}'
            self.send_bytes(
                HTTPStatus.OK,
                payload,
                "application/json; charset=utf-8",
            )
            return
        self.send_bytes(
            HTTPStatus.NOT_FOUND,
            b'{"error":"not found"}',
            "application/json; charset=utf-8",
        )

    def send_bytes(
        self,
        status: HTTPStatus,
        payload: bytes,
        content_type: str,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self' 'unsafe-inline'")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format_string: str, *args: Any) -> None:
        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        print(f"{timestamp} HTTP {format_string % args}")


def serve(args: argparse.Namespace) -> int:
    build_snapshot(args.database, args.workspace_storage)
    server = DeckServer((args.host, args.port), DeckHandler)
    server.database = args.database
    server.workspace_storage = args.workspace_storage
    server.page = build_html(args.poll_ms)
    url = f"http://{args.host}:{args.port}/"
    print("Cursor read-only web deck POC M1.1")
    print(f"URL: {url}")
    print(f"Database: {args.database}")
    print("Safety: mode=ro, query_only=true, actions=false, launch=false")
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
        if args.json:
            snapshot = build_snapshot(
                args.database,
                args.workspace_storage,
            )
            print(json.dumps(snapshot_payload(snapshot), indent=2, sort_keys=True))
            return 0
        return serve(args)
    except (FileNotFoundError, RuntimeError, sqlite3.Error, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
