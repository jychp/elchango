# elChango

<p align="center">
  <img src="docs/assets/elchango-logo.png" alt="elChango cybernetic monkey logo" width="320">
</p>

elChango is a local control deck for native AI coding sessions. A signed macOS
menu bar app discovers Cursor and Claude Code sessions, reduces provider state
to a shared 15-key deck, and serves that deck to a browser and Stream Deck
hardware.

The project has completed its native Swift cutover. Cursor and Claude Code
inventory, state, verified focus, neutral new-session launch, and bounded
semantic commands are implemented. Provider integrations still depend on
version-sensitive local schemas, native shortcuts, and Accessibility markers,
so unsupported or ambiguous conditions fail closed.

## Capabilities

- Inventory persistent Cursor and Claude Code sessions across workspaces.
- Show working, waiting, done, error, idle, and degraded state through a common
  four-color deck model.
- Keep web and Stream Deck pagination independent while sharing ordering,
  preferences, provider selection, and action safety.
- Focus an existing session through a provider-verified route. Cursor verifies
  the exact selected target after dispatch; Claude verifies the exact sidebar
  shortcut and requires a later inventory snapshot before commands are enabled.
- Open Cursor's blank New Agent view or Claude Desktop's neutral new Code view
  without submitting a prompt.
- Dispatch Accept, Open PR, Commit Push, and Compact through provider-owned,
  one-shot recipes after fresh target verification.
- Persist curated session icons and action placement for both surfaces.

## Architecture

The macOS app is the only production host. Python is used only for executable
provider reconnaissance.

```text
macos-app/                 Swift menu bar app, providers, deck service, HTTP API
web/                       Svelte 5 browser surface bundled into the app
plugins/cursor/            Cursor lifecycle hook marketplace plugin
plugins/claude/            Claude Code HTTP hook marketplace plugin
plugins/streamdeck/        Elgato plugin and generated MK.2 profile
contracts/                 HTTP, preference, and provider fixtures
docs/providers/            Evidence, safety boundaries, and limitations
scripts/poc/cursor/        Cursor reconnaissance POCs 01 through 09
scripts/poc/claude/        Claude Code reconnaissance POCs 01 through 04
```

The app binds only to `http://127.0.0.1:8765`. It owns provider inventory,
sanitized hook state, serialized native automation, persistent preferences,
bundled web assets, and the loopback API consumed by both surfaces.

## Requirements

For normal use:

- macOS 14 or newer;
- Cursor and/or Claude Desktop with Claude Code sessions;
- the native elChango app;
- Stream Deck 7.1 or newer only for the hardware surface.

For development:

- Xcode 26 or newer;
- Node.js 24 and npm;
- Python 3 for POCs and provider plugin validation;
- the Elgato Stream Deck CLI, installed through the plugin's npm dependencies.

Python is not required to run the packaged app.

## Install the macOS app

Build from a clone:

```bash
make setup
ELCHANGO_SIGN_MODE=adhoc make build-app-macos
open macos-app/dist/elChango.app
```

Move `macos-app/dist/elChango.app` to `/Applications/elChango.app` before
installing the Cursor plugin. The plugin intentionally uses that fixed path for
its fail-open hook reporter.

Ad-hoc signing is suitable for builds and unprivileged HTTP smoke tests. For
Accessibility testing and regular use, build with a stable Apple Development or
local signing identity and keep the app at a stable path:

```bash
ELCHANGO_SIGN_MODE=identity \
ELCHANGO_CODESIGN_IDENTITY="Your Signing Identity" \
make build-app-macos
```

Launch the app and grant Accessibility permission when prompted. The menu bar
item reports service status and opens the browser deck.

## Install provider plugins

Provider plugins add low-latency lifecycle hooks. They are fail-open: an absent
app or unavailable loopback service does not block the coding agent.

### Cursor marketplace plugin

The repository contains `.cursor-plugin/marketplace.json`. In Cursor's Plugins
settings, add or import `https://github.com/jychp/elchango` as a marketplace,
then install the `elchango` plugin from that marketplace. For local development,
register the cloned repository and select the same plugin.

The plugin invokes
`/Applications/elChango.app/Contents/MacOS/elChangoHookReporter`, so use the
documented application path. See
[plugins/cursor/README.md](plugins/cursor/README.md) for the reported events and
privacy boundary.

### Claude Code marketplace plugin

Run these commands in a shell:

```bash
claude plugin marketplace add jychp/elchango
claude plugin install elchango@elchango
```

For a local clone, replace `jychp/elchango` with its filesystem path. Start
elChango before beginning or resuming a session. See
[plugins/claude/README.md](plugins/claude/README.md) for hook coverage.

## Install the Stream Deck plugin

Build the installable distribution:

```bash
make build-plugin-streamdeck
```

Double-click the generated
`plugins/streamdeck/com.jychp.elchango.streamDeckPlugin`. It installs the plugin
and an `elChango` Stream Deck MK.2 profile with all 15 keys populated. The
profile stores deck positions, not session identities. The running plugin reads
fresh button IDs and revisions from the local app before every action.

For the locked or idle device screen, select
`docs/assets/elchango-screensaver.png` in Stream Deck Settings under Devices,
Set Screensaver. Stream Deck manages that setting outside the plugin.

See [plugins/streamdeck/README.md](plugins/streamdeck/README.md) for development,
runtime, and uninstall details.

## Safety and privacy

- Provider databases are opened read-only, with Cursor additionally using
  `PRAGMA query_only=ON`.
- Hooks retain lifecycle metadata only. Prompt text, responses, tool content,
  notification messages, email, and transcript content are discarded.
- A hook affects state only when its native ID exactly matches a current
  persistent session.
- Surface requests contain opaque provider-qualified targets and stable semantic
  command IDs, never arbitrary prompt text, shortcuts, scripts, or shell
  commands.
- Focus uses provider-specific verification and never enables commands from a
  shortcut response alone.
- Command dispatch rechecks the selected session, frontmost application, and,
  for text recipes, the empty provider-specific composer immediately before one
  dispatch.
- Privileged actions are serialized across providers. Stale revisions,
  ambiguous identity, schema drift, or failed Accessibility checks reject the
  action instead of guessing.
- One unavailable provider does not block the other provider or the local deck.

## Development

The root Makefile is the supported entry point:

```bash
make setup                    # install and resolve dependencies
make                          # run all tests and diff checks
make test-app-macos           # Swift tests
make test-web                 # Svelte and TypeScript checks
make test-plugins             # Cursor, Claude, and Stream Deck plugins
make test-pocs                # compile every POC and exercise --help
make build                    # package the app and all plugins
make build-web                # build browser assets only
make build-plugin-cursor      # validate and package the Cursor plugin
make build-plugin-claude      # validate and package the Claude plugin
make build-plugin-streamdeck  # validate and package the Stream Deck plugin
make clean                    # remove generated distributions
```

For live web development, run the native service and Vite separately:

```bash
swift run --package-path macos-app ElChangoApp
npm --prefix web run dev
```

Vite proxies `/api` to the loopback service. More frontend details are in
[web/README.md](web/README.md).

## Releases

`make release VERSION=X.Y.Z` accepts strict semantic versions without a `v`
prefix. The value must match the committed Stream Deck package and manifest
version. The target requires a clean tracked worktree on `main`, fetches
`origin/main`, verifies that local and remote `main` match, creates annotated
tag `vX.Y.Z`, and pushes that tag.

The tag workflow validates and packages the Stream Deck plugin on Linux, creates
a SHA-256 checksum, and creates or updates the GitHub Release with generated
notes. The current release workflow does not publish the macOS app or provider
plugin archives.

## Provider documentation

- [Cursor provider findings](docs/providers/cursor.md)
- [Claude Code provider findings](docs/providers/claude-code.md)

These documents distinguish measured observations, conclusions, degradation,
and unproven assumptions. The POCs under `scripts/poc/cursor/` and
`scripts/poc/claude/` remain the executable evidence.
