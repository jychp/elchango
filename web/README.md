# elChango web deck

This package is the Svelte 5 browser surface for elChango. It renders the same
fixed 5-column by 3-row deck used by the Stream Deck plugin. The native macOS
app remains the source of truth for provider inventory, ordering, state,
pagination, preferences, target verification, and actions.

## Runtime model

The production build is bundled into `elChango.app` and served by the native
loopback service at `http://127.0.0.1:8765`. The browser does not read Cursor or
Claude data directly and does not perform native automation. Open it from the
app menu. A short-lived, single-use URL creates an `HttpOnly`,
`SameSite=Strict` session cookie without exposing the persistent control token
to browser JavaScript.

The web surface has pagination and provider-picker state independent from the
Stream Deck surface. All browser tabs currently use the same `web` client ID
and therefore share that web state. Key activations send the current button ID
and revision to the native service, which rebuilds the deck and resolves the
target before acting.

## Requirements

- Node.js 24
- npm
- the native elChango service for live data and actions

## Development

Install all repository dependencies from the root:

```bash
make setup
```

Run the native service and Vite in separate terminals:

```bash
swift run --package-path macos-app ElChangoApp
npm --prefix web run dev
```

Start the native app first. Vite reads the owner-only control token from the
application support directory and adds it to proxied `/api` requests. The
token remains in the Vite process and is not included in browser code.

### Demo deck

For a deterministic 15-key deck suitable for documentation screenshots, run
the development server without the native service:

```bash
npm --prefix web run dev -- --open '/?demo=true'
```

The exact URL is `http://localhost:5173/?demo=true`. Demo keys are inert, and
the page neither polls snapshots nor sends action requests. The demo is
statically gated to Vite development builds, so production and packaged builds
ignore the query parameter.

## Checks and builds

From the repository root:

```bash
make test-web
make build-web
```

Or run the package scripts directly:

```bash
npm --prefix web run check
npm --prefix web run build
npm --prefix web run preview
```

`check` runs Svelte diagnostics and TypeScript checks. `build` writes static
assets to `web/dist/`; the macOS packaging script embeds those assets into the
application.

## Design constraints

- Keep provider-specific recipes and native IDs out of the browser.
- Treat button IDs and revisions as short-lived routing values, not
  authorization capabilities.
- Preserve the fixed 15-position layout and shared deck contract.
- Do not infer state from labels, colors, or transcript content.
- Keep client-scoped navigation local to the browser client.
- Make disabled and degraded states explicit rather than optimistic.

The root [README](../README.md) covers installation, safety, provider plugins,
Stream Deck setup, and release behavior.
