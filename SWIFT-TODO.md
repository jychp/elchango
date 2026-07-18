# Native macOS Backend TODO

## Decision

Replace the production Python backend with a native Swift macOS application.
Do not retain a Python-to-Swift accessibility bridge as the durable
architecture.

The web deck and Stream Deck plugin remain separate surfaces and keep their
current loopback API contracts. Python remains useful for executable
reconnaissance POCs under `scripts/poc/`, but it should no longer perform
production accessibility actions after the migration.

## Why

The current Python process requires broad macOS Accessibility permission for
Cursor or the terminal that launches it. A signed native application provides
a dedicated, stable TCC identity and a clearer permission experience.

A permanent bridge would add:

- two production processes and lifecycle management;
- IPC that must be authenticated and versioned;
- a race between target verification and action dispatch;
- duplicated diagnostics and deployment concerns;
- continued dependence on the Python runtime.

Privileged target verification and input dispatch should happen atomically in
one native process.

## Target architecture

Build one signed, non-sandboxed macOS application with a stable bundle
identifier. It owns:

- Cursor and Claude Code Desktop inventory;
- provider health and degradation reporting;
- exact selected-session verification;
- foreground application and focused-input verification;
- `AXUIElement` accessibility queries;
- bounded `CGEvent` keyboard dispatch;
- persistent deck preferences with atomic writes;
- the loopback HTTP API used by the web and Stream Deck surfaces;
- application startup, permission onboarding, and diagnostics.

Use direct macOS APIs instead of AppleScript subprocesses:

- `AXUIElement` and `AXIsProcessTrustedWithOptions` for accessibility;
- `NSWorkspace` for foreground application identity and activation;
- `CGEvent` for verified keyboard sequences;
- SQLite in strict read-only mode for provider inventory;
- Foundation for JSON and preference persistence.

Use a maintained HTTP implementation rather than writing a custom HTTP parser.
Bind only to loopback and preserve the existing API security constraints.

## Safety requirements

- Keep provider-qualified session IDs and semantic command IDs.
- Never accept arbitrary harness text or shortcuts from a surface request.
- Re-read the provider target immediately before every privileged action.
- Verify the expected bundle identifier and exact input marker in the same
  process that sends the event.
- Send each bounded recipe once. Never retry an ambiguous dispatch.
- Report transport acceptance separately from semantic command completion.
- Fail one provider independently without blocking other providers.
- Treat undocumented provider schemas and accessibility markers as versioned,
  fail-closed compatibility points.

## Migration plan

### 1. Freeze contracts

- Capture the current loopback API as contract fixtures.
- Capture representative Cursor and Claude provider snapshots.
- Preserve web and Stream Deck behavior as black-box acceptance tests.

### 2. Create the native host

- Create the signed macOS application and stable bundle identifier.
- Add Accessibility onboarding and a diagnostics screen.
- Add login-item support only after normal launch is reliable.
- Confirm that TCC authorization survives signed application upgrades.

### 3. Port shared state

- Port models, deck layout, pagination, client-scoped picker state, and health.
- Port versioned, atomic preferences without losing existing user settings.
- Keep the on-disk preference format compatible where practical.

### 4. Port providers

- Port Cursor inventory and exact selected-session detection.
- Port Claude Code Desktop inventory and exact selected-session detection.
- Port focus and new-session behavior with existing conservative verdicts.
- Port command recipes and exact target verification.
- Keep provider failures isolated.

### 5. Port the loopback service

- Serve the existing compiled web assets.
- Preserve all current API routes and public response shapes.
- Keep native provider IDs server-side.
- Verify compatibility with the installed Stream Deck plugin.

### 6. Cut over

- Run Python and Swift contract suites against identical fixtures.
- Perform live Cursor and Claude action tests.
- Switch the normal launcher to the native application only after parity.
- Remove production Python runtime requirements.
- Retain Python POCs as executable reconnaissance evidence.

## Acceptance criteria

- Accessibility permission is granted only to the signed elChango application.
- Cursor and terminal do not require Accessibility permission for elChango.
- Web and Stream Deck surfaces work without contract changes.
- Cursor and Claude remain independently available or degraded.
- Focus, Accept, Commit Push, Open PR, and Compact retain exact-target safety.
- Existing preferences migrate without losing action order or session icons.
- Application updates preserve the expected TCC identity.
- Contract, unit, and live provider verification pass before cutover.

## Non-goals

- Rewriting the Svelte web deck as a native UI.
- Rewriting the Stream Deck plugin.
- Publishing through the Mac App Store.
- Abstracting additional providers before a second native adapter requires it.
- Claiming that native code removes the brittleness of undocumented provider
  schemas or accessibility markers.

## Open technical decisions

- Decide whether automatic launch uses `SMAppService`.
- Define signing, notarization, update, and development-certificate workflows.
- Determine whether the current preferences schema remains byte-compatible or
  needs a versioned migration.

## Validated native foundation decisions

- Keep the SwiftPM package isolated under `macos-app/`, with `ElChangoCore`,
  `ElChangoProviders`, and `ElChangoApp` targets.
- Move the hardware integration to `plugins/streamdeck/`. Keep the web deck and
  Python backend in their current locations during migration.
- Compile Cursor and Claude provider modules into the signed application
  process. They are provider adapters, not separately loaded plugins.
- Use a menu bar-only `LSUIElement` application with bundle identifier
  `com.jychp.elchango`.
- Use FlyingFox 0.26.x as the maintained, concurrency-native loopback HTTP
  implementation.
- Use explicit ad-hoc and identity signing modes. Ad-hoc signing is limited to
  build and unprivileged smoke tests; stable identity signing is required for
  TCC persistence tests.
- The first native milestone serves health, a disabled 15-key snapshot, and
  bundled web assets. Provider actions remain fail-closed until their native
  adapters are ported and verified.

## Migration progress

- Complete: signed menu bar foundation, explicit Accessibility onboarding,
  loopback HTTP service, bundled web assets, and contract fixtures.
- Complete: shared deck layout, stable pagination, client-scoped picker state,
  conservative revisions, provider failure isolation, and version 1 preference
  compatibility with atomic persistence.
- Complete: native Cursor inventory, workspace mapping, state inference, and
  exact selected-session detection through a strict read-only SQLite
  transaction. Cursor is registered independently and reports schema or data
  failures without blocking the host.
- Next: port Claude Code Desktop inventory and exact selected-session detection.
  Cursor focus, launch, hooks, and commands remain fail-closed until their
  native action boundaries are ported and verified separately.
