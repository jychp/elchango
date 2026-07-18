# elChango

<p align="center">
  <img src="docs/assets/elchango-logo.png" alt="elChango cybernetic monkey logo" width="320">
</p>

elChango is a local web and Stream Deck command surface for native AI coding
agent sessions.

The native macOS app reads real Cursor and Claude Desktop sessions and renders
the same fixed 5-column by 3-row deck in a browser and on Stream Deck MK.2
hardware. Both surfaces show live state, focus sessions with exact post-action
verification, paginate independently, launch sessions, and dispatch bounded
provider-owned commands.

## Requirements

- macOS
- Xcode 26 or newer for the native host
- Node.js and npm
- Cursor and/or Claude Desktop; unavailable harnesses are skipped independently
- Stream Deck 7.1 or newer for the hardware surface
- Python 3.11 or newer only when running reconnaissance POCs

## Build and run

### Native macOS app

The menu bar app under `macos-app/` is the production host. It owns both native
providers, shared deck state, hooks, Accessibility actions, persisted
personalization, bundled web assets, and the loopback HTTP service.

```bash
npm --prefix web install
ELCHANGO_SIGN_MODE=adhoc macos-app/Scripts/package-app.sh
open macos-app/dist/elChango.app
```

The menu bar shows service status, opens the web deck, and offers an explicit
Accessibility permission request only when authorization is absent. Ad-hoc
signing supports build and HTTP smoke testing only. Use
`ELCHANGO_SIGN_MODE=identity ELCHANGO_CODESIGN_IDENTITY="..."` with a stable
Apple Development or local development identity when testing TCC persistence.
Keep the signed app at a stable path such as `/Applications/elChango.app` so
Accessibility authorization survives normal upgrades. The native host binds
only to <http://127.0.0.1:8765/>.

A missing or incompatible harness does not block the other provider or prevent
elChango from starting. Unavailable providers are reported by `/api/health`.

Long-press a session key to choose a persisted icon from the curated Phosphor
set. Long-press any of the three center action keys to assign Accept, Open PR,
Commit Push, or Compact. Preferences are shared by the web and Stream Deck
surfaces and stored in
`~/Library/Application Support/elChango/preferences.json`.

Cursor and Claude Code Desktop enable Accept, Open PR, Commit Push, and Compact
after live command-dispatch reconnaissance. Every action requires the uniquely
selected session of the frontmost harness. Text and slash recipes additionally
require an empty, enabled, provider-specific composer input. Claude Accept is
an intentional application-level `Cmd+Enter` shortcut and does not require
composer focus. Open PR and Commit Push instruct the native agent; elChango does
not run host-side Git operations for these buttons.

## Stream Deck MK.2

Build, validate, and package the official Elgato plugin:

```bash
npm --prefix plugins/streamdeck install
npm --prefix plugins/streamdeck run check
npm --prefix plugins/streamdeck run pack
```

Double-click `plugins/streamdeck/com.jychp.elchango.streamDeckPlugin`. The
installer includes an automatically installed `elChango` MK.2 profile with all
15 keys populated. Start the native elChango menu bar app; it serves the same
loopback contract used by the web deck.

The plugin uses the sleeping monkey while the local service is offline and the
knocked-out monkey for failed actions. To use the sleeping monkey on the locked
or idle device screen, select
`docs/assets/elchango-screensaver.png` in Stream Deck Settings, Devices, Set
Screensaver. Stream Deck manages this setting outside the plugin SDK.

For plugin development:

```bash
npm --prefix plugins/streamdeck run link
npm --prefix plugins/streamdeck run watch
```

See [plugins/streamdeck/README.md](plugins/streamdeck/README.md) for runtime and uninstall
details.

## Live Cursor activity

SQLite provides session inventory, selection, and persisted results. Cursor
lifecycle hooks provide the low-latency `working`, `done`, and `error`
transitions. Add these fail-open user hooks to `~/.cursor/hooks.json`, replacing
the application path if elChango is installed elsewhere:

```json
{
  "version": 1,
  "hooks": {
    "sessionStart": [
      {
        "command": "/Applications/elChango.app/Contents/MacOS/elChangoHookReporter --provider cursor",
        "timeout": 1,
        "failClosed": false
      }
    ],
    "beforeSubmitPrompt": [
      {
        "command": "/Applications/elChango.app/Contents/MacOS/elChangoHookReporter --provider cursor",
        "timeout": 1,
        "failClosed": false
      }
    ],
    "stop": [
      {
        "command": "/Applications/elChango.app/Contents/MacOS/elChangoHookReporter --provider cursor",
        "timeout": 1,
        "failClosed": false
      }
    ],
    "sessionEnd": [
      {
        "command": "/Applications/elChango.app/Contents/MacOS/elChangoHookReporter --provider cursor",
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

## Live Claude Code activity

Claude Code can post its official hooks directly to the native loopback
service. Add HTTP handlers in `~/.claude/settings.json` for `SessionStart`,
`UserPromptSubmit`, `PermissionRequest`, `Notification`, `Elicitation`,
`ElicitationResult`, `Stop`, `StopFailure`, and `SessionEnd` using:

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "http",
            "url": "http://127.0.0.1:8765/api/hooks/claude-code",
            "timeout": 1
          }
        ]
      }
    ]
  }
}
```

Add the same handler under `PreToolUse` and `PostToolUse` with matcher
`AskUserQuestion|ExitPlanMode`. The native provider accepts hook evidence only
when `session_id` exactly matches a current persistent Claude Code session.

## Frontend development

Build the web application once, then run the service and Vite in separate
terminals:

```bash
swift run --package-path macos-app ElChangoApp
npm --prefix web run dev
```

Vite proxies `/api` to the local service.

## Verification

```bash
npm --prefix web run check
npm --prefix web run build
npm --prefix plugins/streamdeck run check
npm --prefix plugins/streamdeck run validate
swift test --package-path macos-app
macos-app/Scripts/package-app.sh
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
- Provider text dispatch verifies the exact selected session, foreground
  application, and composer input before sending one bounded recipe.
- Privileged actions are serialized across providers. Keyboard events target
  the verified process ID, and stale command revisions cannot retarget a newly
  selected session.
- Undocumented Cursor schema changes fail explicitly instead of guessing.
