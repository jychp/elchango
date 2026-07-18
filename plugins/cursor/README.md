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

## Installation

Install `elchango` from the elChango Cursor marketplace. For local development,
register this repository as a marketplace and select the `elchango` plugin.

The fixed application path is part of the plugin configuration. If elChango is
installed elsewhere, the reporter hook fails open and the plugin cannot report
activity. Move the app to `/Applications/elChango.app` before using the plugin.
