# elChango

elChango is a local web command deck for native AI coding agent sessions.

The v0.1 target reproduces a Stream Deck MK.2 as a fixed 5-column by 3-row web
surface. The first product slice reads real Cursor sessions and renders them as
15 typed deck buttons. Session actions remain disabled until exact targeting is
proven safe enough for the product.

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
read-only Cursor snapshot API from one loopback process.

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
- The foundation service exposes no focus, launch, prompt, or agent-action
  endpoint.
- Undocumented Cursor schema changes fail explicitly instead of guessing.
