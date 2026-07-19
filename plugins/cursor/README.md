# elChango for Cursor

This plugin reports Cursor agent lifecycle events to the native elChango app so
its web and Stream Deck surfaces can display live session state.

## Requirements

- macOS
- Cursor
- elChango installed at `/Applications/elChango.app`
- The elChango app running locally

## Hooks

The plugin registers fail-open handlers for `sessionStart`,
`beforeSubmitPrompt`, `stop`, and `sessionEnd`. Each handler invokes:

```text
/Applications/elChango.app/Contents/MacOS/elChangoHookReporter --provider cursor
```

Handlers time out after one second and never block Cursor when the reporter or
local service is unavailable.

The reporter forwards only event name, conversation ID, generation ID, composer
mode, and stop status to elChango at `127.0.0.1`. It discards prompt text,
responses, tool data, email, and transcript paths.

Hook routes cannot execute actions. They validate current persistent session
identity, accept only sanitized lifecycle fields, and are rate-limited. They
are intentionally separate from the authenticated control API. See the root
[security policy](../../SECURITY.md) for the complete trust boundary.

## Installation

Individual users will install `elchango` from Cursor's public Marketplace after
it is published. For local development:

```bash
mkdir -p ~/.cursor/plugins/local
ln -s /path/to/elchango/plugins/cursor ~/.cursor/plugins/local/elchango
```

Restart Cursor or run `Developer: Reload Window`, then verify the plugin in
Customize and inspect the Hooks output channel. Teams and Enterprise
organizations may import the repository through a managed Team Marketplace.

The fixed application path is part of the plugin configuration. If elChango is
installed elsewhere, the reporter hook fails open and the plugin cannot report
activity. Move the app to `/Applications/elChango.app` before using the plugin.
