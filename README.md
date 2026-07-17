# elChango

elChango is a local web command deck for native AI coding agent sessions.

The v0.1 target reproduces a Stream Deck MK.2 as a fixed 5-column by 3-row web
surface. The first product slice reads real Cursor sessions and renders them as
15 typed deck buttons. Session actions remain disabled until exact targeting is
proven safe enough for the product, while New opens a blank agent view for
manual prompt entry.

## Requirements

- macOS
- Python 3.11 or newer
- Node.js and npm
- Cursor with at least one local agent session

## Build and run

```bash
npm --prefix web install
npm --prefix web run build
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
chango serve
```

Open <http://127.0.0.1:8765/>.

The production command serves both the compiled Svelte application and the
Cursor-backed loopback API from one process. All SQLite access remains
read-only.

## Live Cursor activity

SQLite provides session inventory, selection, and persisted results. Cursor
lifecycle hooks provide the low-latency `working`, `done`, and `error`
transitions. Add these fail-open user hooks to `~/.cursor/hooks.json`, replacing
`/absolute/path/to/chango` with the output of `command -v chango`:

```json
{
  "version": 1,
  "hooks": {
    "sessionStart": [
      {
        "command": "/absolute/path/to/chango report-hook",
        "timeout": 1,
        "failClosed": false
      }
    ],
    "beforeSubmitPrompt": [
      {
        "command": "/absolute/path/to/chango report-hook",
        "timeout": 1,
        "failClosed": false
      }
    ],
    "stop": [
      {
        "command": "/absolute/path/to/chango report-hook",
        "timeout": 1,
        "failClosed": false
      }
    ],
    "sessionEnd": [
      {
        "command": "/absolute/path/to/chango report-hook",
        "timeout": 1,
        "failClosed": false
      }
    ]
  }
}
```

The reporter forwards only event name, conversation ID, generation ID, composer
mode, and stop status to the loopback service. Prompt text, responses, tool
data, email, and transcript paths are discarded. If the service is unavailable,
the reporter
returns immediately and never blocks Cursor.

## Frontend development

Build the web application once, then run the service and Vite in separate
terminals:

```bash
PYTHONPATH=src python -m elchango serve
npm --prefix web run dev
```

Vite proxies `/api` to the local service.

## Verification

```bash
PYTHONPATH=src python -m unittest discover -s tests
npm --prefix web run check
npm --prefix web run build
```

## Current safety boundary

- Cursor SQLite access uses read-only mode and `PRAGMA query_only=ON`.
- The HTTP server accepts loopback bind addresses only.
- Hook events affect a session only when their conversation ID exactly matches a
  session ID observed in SQLite. Unmatched events are ignored.
- Session focus uses native Cursor shortcuts and requires exact post-action
  verification.
- Pagination intents never modify Cursor state.
- New opens Cursor's blank New Agent view. It does not submit a prompt or claim
  that a persisted composer exists before the user takes over.
- Prompt dispatch and agent actions remain disabled.
- Undocumented Cursor schema changes fail explicitly instead of guessing.
