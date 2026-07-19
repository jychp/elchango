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

Install the latest release with Homebrew:

```bash
brew install --cask jychp/tap/elchango
```

Official GitHub releases include
`elChango-X.Y.Z-macos-universal.zip` for Apple Silicon and Intel Macs. The app
is signed with Developer ID, notarized by Apple, and accompanied by a SHA-256
checksum. After verifying the checksum, extract the archive and move
`elChango.app` to `/Applications`.

To build from a clone instead:

```bash
make setup
make build-app-macos
open macos-app/dist/elChango.app
```

Move `macos-app/dist/elChango.app` to `/Applications/elChango.app` before
installing the Cursor plugin. The plugin intentionally uses that fixed path for
its fail-open hook reporter.

Ad hoc signing is suitable for builds and unprivileged HTTP smoke tests. For
Accessibility testing during development, build with a stable Apple Development
or local signing identity and keep the app at a stable path:

```bash
ELCHANGO_SIGN_MODE=identity \
ELCHANGO_CODESIGN_IDENTITY="Your Signing Identity" \
make build-app-macos
```

Launch the app and grant Accessibility permission when prompted. The menu bar
item reports service status and opens the browser deck. Open the web deck from
that menu: the app uses a short-lived bootstrap URL to create an authenticated
browser session.

## Install provider plugins

Provider plugins add low-latency lifecycle hooks. They are fail-open: an absent
app or unavailable loopback service does not block the coding agent.

### Cursor marketplace plugin

Individual users install reviewed plugins from Cursor's public Marketplace.
elChango is not yet listed there, so local development currently uses a symlink:

```bash
mkdir -p ~/.cursor/plugins/local
ln -s /path/to/elchango/plugins/cursor ~/.cursor/plugins/local/elchango
```

Restart Cursor or run `Developer: Reload Window`, then confirm `elchango`
appears in Customize and its Hooks panel. Teams and Enterprise organizations
can instead import `https://github.com/jychp/elchango` through their managed
Team Marketplace. Repository import is not an individual-user installation
flow.

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

- The loopback API requires a per-install control token stored in an owner-only
  file. Stream Deck uses Bearer authentication; the web deck receives an
  `HttpOnly`, `SameSite=Strict` session cookie through a single-use URL.
- Requests must use the expected loopback `Host`; foreign browser origins are
  rejected. Hook routes are nonprivileged, bounded, sanitized, inventory-gated,
  and rate-limited.
- Provider databases are opened read-only, with Cursor additionally using
  `PRAGMA query_only=ON`.
- Hooks retain lifecycle metadata only. Prompt text, responses, tool content,
  notification messages, email, and transcript content are discarded.
- A hook affects state only when its native ID exactly matches a current
  persistent session.
- Surface requests contain provider-qualified targets and stable semantic
  command IDs, never arbitrary prompt text, shortcuts, scripts, or shell
  commands. Those local identifiers are not authorization secrets.
- Focus uses provider-specific verification and never enables commands from a
  shortcut response alone.
- Command dispatch rechecks the selected session, frontmost application, and,
  for text recipes, the empty provider-specific composer immediately before one
  dispatch.
- Privileged actions are serialized across providers. Stale revisions,
  ambiguous identity, schema drift, or failed Accessibility checks reject the
  action instead of guessing.
- One unavailable provider does not block the other provider or the local deck.

The complete threat model, permission details, disclosure process, known
limitations, and token-rotation procedure are in
[SECURITY.md](SECURITY.md).

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
make build-app-macos-universal # build an ad hoc Universal 2 app
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
[web/README.md](web/README.md). Start the native app first so Vite can read the
local control token without exposing it to browser JavaScript.

## Releases

`VERSION` is the single source of truth for the monorepo release train. The
macOS app, bundled web surface, Cursor plugin, Claude plugin, and Stream Deck
package all use that version. Elgato's four-part manifest expresses the same
release with a trailing `.0`. `make test-versions` and CI reject any divergence.

`make release VERSION=X.Y.Z` accepts strict semantic versions without a `v`
prefix. The value must match the committed root `VERSION` and every component.
The target requires a clean tracked worktree on `main`, fetches
`origin/main`, verifies that local and remote `main` match, creates annotated
tag `vX.Y.Z`, and pushes that tag.

The tag workflow requires the tagged commit to exactly match `origin/main`. It
publishes one GitHub Release containing the Stream Deck plugin and a signed,
notarized Universal 2 macOS ZIP, each with a SHA-256 checksum. A manual workflow
run from `main` exercises the complete signing and notarization path without
publishing a release. After a tagged release is published, the workflow
dispatches an update to `jychp/homebrew-tap`. The tap independently verifies the
archive and checksum, opens a Cask pull request, and auto-merges it only after
Intel and Apple Silicon installation checks pass.

Certificate provisioning, GitHub environment secrets, local release checks, and
the Homebrew-compatible artifact contract are documented in
[docs/releasing.md](docs/releasing.md).

## License and trademarks

The software and documentation are available under the
[MIT License](LICENSE). Bundled dependency licenses are recorded in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

The elChango name, monkey logo, application icon, screensaver artwork, and
other project branding are not licensed under MIT. See
[TRADEMARKS.md](TRADEMARKS.md) for permitted use and the third-party
non-affiliation statement.

## Provider documentation

- [Cursor provider findings](docs/providers/cursor.md)
- [Claude Code provider findings](docs/providers/claude-code.md)

These documents distinguish measured observations, conclusions, degradation,
and unproven assumptions. The POCs under `scripts/poc/cursor/` and
`scripts/poc/claude/` remain the executable evidence.
