# elChango Stream Deck plugin

This package is the official Elgato Stream Deck surface for elChango v0.2. It
mirrors the fixed 5 by 3 web deck on a Stream Deck MK.2. The Python service
remains the source of truth for session ordering, state, pagination, focus, and
New Agent behavior.

## Requirements

- macOS 13 or newer
- Stream Deck 7.1 or newer
- Node.js 20.5.1 or newer for development
- A running local elChango service on `http://127.0.0.1:8765`

The distributed plugin uses the Node.js runtime embedded by Stream Deck. Users
do not need Node.js to install the packaged plugin.

## Development

```bash
npm --prefix plugins/streamdeck install
npm --prefix plugins/streamdeck run check
npm --prefix plugins/streamdeck run validate
npm --prefix plugins/streamdeck run link
npm --prefix plugins/streamdeck run restart
```

`npm --prefix plugins/streamdeck run watch` rebuilds the plugin and restarts it after
each successful build.

## Installable package

```bash
npm --prefix plugins/streamdeck run pack
```

The command validates the manifest and writes a `.streamDeckPlugin` installer
under `plugins/streamdeck/`. Double-click that file to install the plugin and accept the
bundled `elChango` MK.2 profile.

The profile assigns the same elChango action to every key. Runtime key
coordinates determine which position from the 15-button snapshot is rendered,
so no session identity is persisted in the profile.

## Runtime behavior

- One plugin-wide loop polls the loopback snapshot API every second while an
  elChango key is visible.
- Each key receives an SVG image containing its current color, icon, label,
  detail, enabled state, and selected state.
- Key presses send the current button ID and revision to the unified activation
  endpoint. The Python service resolves the button again before acting.
- If the service is unavailable, all visible keys show `Offline` and reconnect
  with bounded backoff.
- `New` and `Available` only open Cursor's blank New Agent view. They never
  submit a prompt.

## Uninstall

Remove the plugin in Stream Deck preferences, or unlink a development install:

```bash
npx --yes @elgato/cli unlink com.jychp.elchango
```
