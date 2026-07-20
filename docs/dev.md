# Development guide

This guide covers the elChango architecture, toolchain, builds, tests, local
development, and package validation. Read [CONTRIBUTING.md](../CONTRIBUTING.md)
before proposing or submitting a change.

## Architecture

The native macOS app is the only production host. It owns provider inventory,
sanitized lifecycle state, target verification, serialized native automation,
preferences, bundled web assets, and the authenticated loopback API. The
browser and Stream Deck packages are clients of that service.

```text
macos-app/                 Swift menu bar app, providers, deck service, HTTP API
web/                       Svelte 5 browser surface bundled into the app
plugins/cursor/            Cursor lifecycle hook marketplace plugin
plugins/claude/            Claude Code HTTP hook marketplace plugin
plugins/streamdeck/        Elgato plugin and generated MK.2 profile
contracts/                 HTTP, preference, and provider fixtures
docs/providers/            Provider evidence, safety boundaries, and limitations
```

The app binds only to `http://127.0.0.1:8765`. Web and Stream Deck use one deck
contract but retain independent pagination and provider-picker state. Python
exists for provider reconnaissance and repository validation, not as a
production runtime.

Provider adapters own native session identity, inventory, focus, launch, and
fixed semantic command recipes. Surface clients receive provider-qualified
routing values but cannot submit arbitrary prompts, scripts, shortcuts, or
shell commands. A privileged action must rebuild the requested button, verify
the exact provider and native session, verify the provider-specific command
target, and preserve conservative post-dispatch reporting.

## Toolchain

- macOS 14 or newer.
- Xcode 26 or newer with Swift 6.2.
- Node.js 24 and npm.
- Python 3.
- Claude CLI for Claude's official strict plugin validation. The repository
  validator still runs when the CLI is unavailable.
- Elgato Stream Deck CLI, installed through
  `plugins/streamdeck/package-lock.json`.
- Stream Deck 7.1 or newer and MK.2 hardware for complete device testing.

Install and resolve repository dependencies:

```bash
make setup
```

## Make targets

The root Makefile is the supported entry point.

| Target | Purpose |
| --- | --- |
| `make` or `make test` | Run version checks, Swift tests, web checks, all plugin checks, release-script checks, and `git diff --check`. |
| `make test-versions` | Verify that every package version matches `VERSION`. |
| `make test-app-macos` | Run Swift formatting checks and test suites. |
| `make test-web` | Run ESLint, Prettier, Svelte, TypeScript, and browser behavior tests. |
| `make test-plugins` | Validate all three plugins. |
| `make test-plugin-cursor` | Validate Cursor plugin structure and versions. |
| `make test-plugin-claude` | Run repository validation and Claude CLI strict validation when available. |
| `make test-plugin-streamdeck` | Check licenses, TypeScript, tests, generated profile, and Elgato manifest. |
| `make build` | Package the debug macOS app and all plugins. |
| `make build-app-macos` | Build the current-architecture debug app. |
| `make build-app-macos-universal` | Build the ad hoc signed Universal 2 stable app. |
| `make notarize-app-macos` | Build, sign, submit, and staple the stable release app. |
| `make verify-release-app-macos` | Verify release profile, architectures, signature, and notarization. |
| `make build-web` | Build static browser assets into `web/dist/`. |
| `make build-plugins` | Build all provider and Stream Deck plugin packages. |
| `make build-plugin-cursor` | Validate and package the Cursor plugin. |
| `make build-plugin-claude` | Validate and package the Claude plugin. |
| `make build-plugin-streamdeck` | Validate and package the Stream Deck plugin. |
| `make clean` | Remove generated app, web, and plugin distributions. |

`make release VERSION=X.Y.Z` is a maintainer operation. The complete process,
credentials, and artifact contract are documented in
[releasing.md](releasing.md).

## macOS app development

Build and open the isolated debug profile:

```bash
make build-app-macos
open macos-app/dist/elChango-debug.app
```

The debug profile uses:

- bundle ID `com.jychp.elchango.debug`;
- app name and executable `elChango-debug`;
- application support directory
  `~/Library/Application Support/elChango-debug/`.

The stable profile uses `com.jychp.elchango`, `elChango`, and
`~/Library/Application Support/elChango/`. Stable and debug profiles share
port `8765`, so they cannot run concurrently.

The packaging script automatically uses the first available Apple Development
or Developer ID identity, which gives repeated Accessibility testing a stable
code identity. It falls back to ad hoc signing when no suitable identity is
installed. You can select an identity explicitly:

```bash
ELCHANGO_SIGN_MODE=identity \
ELCHANGO_CODESIGN_IDENTITY="Your Signing Identity" \
make build-app-macos
```

Keep the signed app at a stable path. The Cursor plugin specifically invokes
`/Applications/elChango.app/Contents/MacOS/elChangoHookReporter`, so end-to-end
Cursor hook testing requires a stable-profile app at that path.

Run Swift tests directly when a focused iteration is useful:

```bash
swift test --package-path macos-app
```

## Web development

The production web build is embedded in the app. For live browser development,
start a packaged native service and Vite in separate terminals:

```bash
make build-app-macos
open macos-app/dist/elChango-debug.app
```

```bash
npm --prefix web run dev
```

Vite proxies `/api` to the loopback service. It reads the owner-only local
control token in the Vite process and does not include that token in browser
JavaScript. Stop the packaged app before starting a stable app because both use
the same port.

Useful package commands:

```bash
npm --prefix web run check
npm --prefix web run lint
npm --prefix web run format:check
npm --prefix web test
npm --prefix web run build
npm --prefix web run preview
```

Keep provider-specific native IDs and recipes out of the browser. Preserve the
fixed 15-position contract, explicit disabled and degraded states, and
client-scoped navigation. See [web/README.md](../web/README.md) for the runtime
model.

## Provider plugin development

Validate and package provider plugins from the repository root:

```bash
make test-plugin-cursor
make test-plugin-claude
make build-plugin-cursor
make build-plugin-claude
```

Provider plugins report bounded lifecycle metadata only. They must remain
fail-open when the app is absent and must not gain access to privileged action
routes.

For local Cursor plugin testing:

```bash
mkdir -p ~/.cursor/plugins/local
ln -s /path/to/elchango/plugins/cursor ~/.cursor/plugins/local/elchango
```

Restart Cursor or run **Developer: Reload Window**, then inspect the Hooks
output. The fixed stable application path does not target `elChango-debug`.

For local Claude Code testing, add the clone as a marketplace:

```bash
claude plugin marketplace add /path/to/elchango
claude plugin install elchango@elchango
```

Start elChango before beginning or resuming a session. Plugin-specific event
coverage is documented in
[plugins/cursor/README.md](../plugins/cursor/README.md) and
[plugins/claude/README.md](../plugins/claude/README.md).

## Stream Deck development

```bash
npm --prefix plugins/streamdeck run check
npm --prefix plugins/streamdeck run validate
npm --prefix plugins/streamdeck run link
npm --prefix plugins/streamdeck run watch
npm --prefix plugins/streamdeck run restart
```

`watch` rebuilds and restarts the linked plugin after each successful build.
`validate` regenerates the 15-key profile, builds the plugin, and runs Elgato
manifest validation. `pack` validates and creates the installable
`.streamDeckPlugin` distribution.

The profile stores positions, not session identities. Runtime requests must use
fresh button IDs and revisions. See
[plugins/streamdeck/README.md](../plugins/streamdeck/README.md) for linking,
runtime behavior, and uninstall steps.

## Provider evidence

Undocumented provider integrations must be established with focused evidence
before production abstractions are extended. All findings, observations, and
limitations live in `docs/providers/<provider>.md`, which follows the required
structure in
[`.agents/skills/new-harness/templates/provider-doc.md`](../.agents/skills/new-harness/templates/provider-doc.md)
and opens with a feature-coverage table.
[`docs/providers/feature-matrix.md`](providers/feature-matrix.md) compares the
providers side by side.

Each provider doc must distinguish observations from conclusions and
assumptions, document safety and side effects, record a conservative verdict per
feature, and explain how the integration fails when an external schema no longer
matches. Back inventory and ordering claims with versioned fixtures under
`contracts/providers/<provider>/v1/`, shared with the Swift tests.

Use synthetic or sanitized fixtures. Never commit provider databases,
transcripts, prompts, tokens, credentials, private code, or personal workspace
paths.

## Package validation

`macos-app/Scripts/package-app.sh` builds web assets and Swift products,
assembles the app bundle, embeds the license and notices, signs the helper and
outer app, and invokes `verify-package.sh`.

The package profiles are controlled by environment variables:

- `ELCHANGO_PROFILE`: `debug` or `stable`.
- `ELCHANGO_SIGN_MODE`: `auto`, `adhoc`, `identity`, or `developer-id`.
- `ELCHANGO_CODESIGN_IDENTITY`: required for identity and Developer ID signing.
- `ELCHANGO_ARCHITECTURES`: space-separated `arm64` and/or `x86_64`.
- `ELCHANGO_CONFIGURATION`: Swift build configuration, default `release`.
- `ELCHANGO_BUILD`: positive bundle build number.

Validation checks the profile-specific bundle ID and executable names, root
version, minimum macOS version, app icon, bundled web index, embedded license
and notices, requested architectures, signatures, and absence of
`get-task-allow`.

Distribution verification additionally requires a Developer ID Application
signature, Hardened Runtime, and Apple timestamp. Notarization verification
checks the stapled ticket and Gatekeeper acceptance:

```bash
make verify-release-app-macos
```

Release signing and notarization require maintainer credentials. See
[releasing.md](releasing.md).
