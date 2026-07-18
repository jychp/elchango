# elChango

<p align="center">
  <img src="docs/assets/elchango-logo.png" alt="elChango cybernetic monkey logo" width="320">
</p>

elChango is a local web and Stream Deck command surface for native AI coding
agent sessions.

The v0.2 product reads real Cursor sessions and renders the same fixed 5-column
by 3-row deck in a browser and on Stream Deck MK.2 hardware. Both surfaces show
live state, focus sessions with exact post-action verification, paginate
independently, and open a blank New Agent view for manual prompt entry.

## Requirements

- macOS
- Python 3.11 or newer
- Node.js and npm
- Cursor and/or Claude Desktop; unavailable harnesses are skipped independently
- Stream Deck 7.1 or newer for the hardware surface

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
provider-neutral loopback API from one process. A missing or incompatible
harness does not block other providers or prevent elChango from starting.
Unavailable providers are reported by `/api/health`. All SQLite access remains
read-only.

Long-press a session key to choose a persisted icon from the curated Phosphor
set. Long-press any of the three center action keys to assign Accept, Create PR,
Commit Push, or Compact. Preferences are shared by the web and Stream Deck
surfaces and stored in
`~/Library/Application Support/elChango/preferences.json`.

Cursor enables Accept, Create PR, Commit Push, and Compact after live
command-dispatch reconnaissance. A command is sent only to the uniquely
selected session of the frontmost harness after target and composer-input
verification. Create PR and Commit Push instruct the native agent; elChango
does not run host-side Git operations for these buttons. Other providers keep
commands disabled until their own mappings and input identity are proven.

## Stream Deck MK.2

Build, validate, and package the official Elgato plugin:

```bash
npm --prefix streamdeck install
npm --prefix streamdeck run check
npm --prefix streamdeck run pack
```

Double-click `streamdeck/com.jychp.elchango.streamDeckPlugin` and accept the
bundled `elChango` MK.2 profile. Start `chango serve` normally, or use
`chango serve --api-only` when only the hardware surface is needed.

The plugin uses the sleeping monkey while the local service is offline and the
knocked-out monkey for failed actions. To use the sleeping monkey on the locked
or idle device screen, select
`docs/assets/elchango-screensaver.png` in Stream Deck Settings, Devices, Set
Screensaver. Stream Deck manages this setting outside the plugin SDK.

For plugin development:

```bash
npm --prefix streamdeck run link
npm --prefix streamdeck run watch
```

See [streamdeck/README.md](streamdeck/README.md) for runtime and uninstall
details.

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
npm --prefix streamdeck run check
npm --prefix streamdeck run validate
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
- Cursor prompt dispatch verifies the exact selected session, foreground
  application, and composer input before sending one bounded recipe.
- Undocumented Cursor schema changes fail explicitly instead of guessing.
