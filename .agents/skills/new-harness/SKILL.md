---
name: new-harness
description: Adds a new AI coding harness provider to elChango from reconnaissance through verified inventory, state, focus, launch, deck integration, tests, and documentation. Use when adding or extending support for Cursor, Claude Code, or another local agent harness.
---

# Add a new harness

Follow this sequence. Do not skip reconnaissance or weaken the targeting safety
barrier to make an integration appear complete.

## 1. Confirm scope with the user

Before editing product code, agree on:

- the exact product and session type to support;
- which capabilities are in scope: inventory, state, focus, launch, and
  personalized commands;
- which real-session experiments can run safely now;
- the expected local data roots and supported operating systems.

Treat `PROJECT.md` as brainstorming, not a requirement. Record validated
provider-specific findings in `docs/providers/<provider>.md`.

## 2. Build executable reconnaissance

Create numbered, self-documenting Python POCs under
`scripts/poc/<provider>/` before building the adapter. Number each provider's
POCs independently from `01`. Follow the POC standard in `AGENTS.md`.

Establish evidence for each capability separately:

1. Inventory all persistent sessions, not only live processes.
2. Identify the stable native session ID.
3. Map sessions to workspace paths.
4. Find authoritative activity signals, preferably official hooks.
5. Determine whether exact focus can be verified after acting.
6. Determine whether an official new-session mechanism exists.
7. Determine the exact provider-specific command target: prompt-input focus for
   text recipes, or proven application-level scope for shortcuts.
8. Search official evidence for each command mapping; record absent evidence
   rather than inferring a slash command, shortcut, or prompt.

Keep observations, conclusions, and hypotheses distinct. Do not infer current
state from transcript text. Do not enable focus from a shortcut or deep link
unless exact post-action identity can be verified.

For command reconnaissance, use only stable semantic IDs already validated for
the shared contract. Keep provider recipes outside surfaces. A POC must require
an explicit recipe when no official mapping is available, default to dry-run,
require `--execute`, recheck the session, frontmost application, and exact
command target immediately before one dispatch, and never retry an ambiguous
result. Use the `new-command` skill for the full workflow.

## 3. Define the provider identity

Choose a stable lowercase `provider_id` without `:`. Public session IDs are
automatically qualified as `<provider_id>:<native_id>`.

Add provider presentation metadata:

```swift
let descriptor = ProviderDescriptor(
    id: "example",
    displayName: "Example",
    icon: .example,
    capabilities: [.focusSession, .newSession]
)
```

Declare only capabilities proven by evidence. The new-session picker discovers
providers dynamically from `.newSession` capability declarations.

## 4. Implement the provider boundary

Implement `AgentProvider` from
`macos-app/Sources/ElChangoCore/Models/ProviderModels.swift` in a dedicated
module under `macos-app/Sources/ElChangoProviders/`.

Required methods:

- `snapshot() async throws -> ProviderSnapshot`
- `isFrontmost() async throws -> Bool`
- `focus(nativeSessionID:) async throws -> ProviderActionResult`
- `openNew() async throws -> ProviderActionResult`

Use a provider-specific `Error` type. Fail explicitly when local
files, schemas, identifiers, or mappings differ from observed evidence.

For every `AgentSession`:

- keep `native_id` provider-native;
- supply provider-qualified capabilities per session;
- use provider-neutral state and confidence values;
- supply an exact `last_activity_at_ms` for cross-provider ordering;
- mark `selected=True` only from unique, authoritative evidence.

Cache expensive immutable metadata only when invalidation is explicit, such as
file modification time plus size.

## 5. Implement state signals

If the harness has lifecycle hooks, create a provider-specific activity store
(add it to `macos-app/Sources/ElChangoCore/Models/ActivityStores.swift`
alongside the existing stores) and expose a loopback hook recorder through:

Implement `recordHook(_:observedAtMilliseconds:)` on the provider and route it
through the existing provider registry. The HTTP route `/api/hooks/<provider_id>`
is already provider-agnostic (`FoundationHTTPHandler` -> `DeckService.recordHook`
looks the provider up by id), so no surface change is needed.

The relay mechanism depends on the harness's hook type:

- If the harness supports HTTP hooks (as Claude Code does), the plugin can POST
  directly to `http://127.0.0.1:8765/api/hooks/<provider_id>`.
- If the harness only supports command hooks (as Cursor and Codex do), add a
  `case` for the provider to `macos-app/Sources/ElChangoHookReporter/main.swift`
  with a strict sanitized key allow-list. The plugin invokes
  `elChangoHookReporter --provider <provider_id>`, which POSTs the sanitized
  payload. Read the harness's official hook docs first: capture the exact event
  names, payload field names, and whether an HTTP hook type exists; record
  absent evidence rather than inferring.

Map signals conservatively to:

- blue: `working`;
- orange: `waiting`;
- red: rendered terminal `error`;
- green: `done`;
- gray: `idle` (and `unknown`, which has no dedicated color and renders gray).

Live state comes only from hooks. A session with no live hook signal must be
`idle` at persisted confidence, exactly as the Claude Code and Codex providers do
(`state: activity?.0 ?? .idle`). Never derive `working`/`waiting`/`done` from
persisted files (a rollout tail, a database row): doing so paints the deck with a
stale green/blue state by default. Persisted files may still be read to order
sessions by last activity, but never to set a state. There is no `unknown`
`DeckColor`; a `SessionState.unknown` (for example a stale-expired hook) maps to
gray via `DeckLayout.displayColor`.

Expire stale working or waiting signals when an expected terminal event never
arrives. Retain metadata only, never prompts, responses, or transcript content.

## 6. Implement privileged actions safely

### Focus

Before acting:

1. Resolve the public button to the current provider and native session ID.
2. Confirm the session still exists and is focus-capable.
3. Capture the authoritative pre-action identity evidence.
4. Execute the narrowest provider-native action.
5. Verify the exact target after the action.

Return `accepted=True` only for an exact verified target. Otherwise return a
conservative verdict and keep the deck action rejected.

### New session

Prefer an official deep link or documented API. Opening a composer is allowed;
submitting a prompt remains manual. Return structured evidence describing what
was requested and what still requires user confirmation.

### Personalized commands

Do not declare a command capability from focus evidence alone. Establish:

1. a fresh public-button to provider-native target resolution;
2. exact selected-session and frontmost-application evidence;
3. exact command-target evidence: agent prompt-input focus for text recipes, or
   an explicitly validated application-level shortcut scope;
4. an official provider recipe or an explicitly configured local recipe;
5. one dispatch with no fallback or automatic retry;
6. a conservative post-dispatch verdict that does not claim semantic completion
   without provider evidence.

Surfaces emit stable semantic IDs only. They must never supply arbitrary prompt,
shortcut, command, or script strings.

## 7. Register the provider

Update `macos-app/Sources/ElChangoProviders/ProviderRegistry.swift` to:

1. instantiate the activity store and provider;
2. inject the shared `NativeAutomation` and `PrivilegedActionGate` when needed;
3. add the provider to the registry without coupling its availability to other
   providers.

The `ElChangoProviders` SwiftPM target uses directory-based sources, so a new
file under `macos-app/Sources/ElChangoProviders/<Provider>/` is picked up
automatically; no `Package.swift` edit is needed.

Do not add provider-specific branches to `DeckService`, the HTTP surfaces, web,
or Stream Deck. Routing must use provider metadata and capabilities.

If the provider ships a hook plugin, also wire it so `make test` covers it:

1. add `plugins/<provider>/` mirroring an existing plugin: its manifest (for
   example `.codex-plugin/plugin.json` with `"hooks": "./hooks/hooks.json"`),
   `hooks/hooks.json`, `README.md`, and `CHANGELOG.md`;
2. add the root marketplace file `.<provider>-plugin/marketplace.json`
   (`source` = `./plugins/<provider>`, versions matching `VERSION`);
3. add a `validate_<provider>()` and a CLI choice to
   `scripts/validate_provider_plugins.py`, asserting the exact event set and the
   fail-open hook contract;
4. add the manifest and marketplace to the version checks in
   `scripts/validate_versions.py`;
5. add a `test-plugin-<provider>` target to the `Makefile`, add it to
   `test-plugins`, and add the provider's POC glob to `test-pocs`.
6. add a read-only `<Provider>PluginInspector` in
   `macos-app/Sources/ElChangoCore/System/` that classifies whether the elChango
   plugin is installed (missing / matching / mismatched / managed / malformed /
   unreadable), modeled on `CursorPluginInspector`/`CodexPluginInspector` (which
   read the marketplace cache) or `ClaudeCodePluginInspector` (which uses the
   harness CLI). Detect the host app by bundle id with
   `NSWorkspace.urlForApplication`. Surface it in `AppDelegate` Diagnostics and
   in the `providerStatuses` map.
7. add a `<Provider>PluginInstaller` (and menu Install/Update actions) only when
   the harness exposes an official, evidence-backed install command. Verify it
   from the harness CLI's own `--help` before implementing (for example
   `codex plugin add PLUGIN@MARKETPLACE` after `codex plugin marketplace add
   owner/repo`, mirrored on `ClaudePluginInstaller`); pass identifiers as fixed
   argument arrays with no shell. If no official command exists, keep the
   provider inspect-only (as Cursor is) rather than inventing an install path.

## 8. Add the icon across contracts

If the provider needs a new icon:

1. add the icon name to the `DeckIconName` enum in the single source of truth,
   `contracts/http/v1/openapi.json`;
2. run `python3 scripts/generate_http_contracts.py` to regenerate the three
   contract files (`macos-app/Sources/ElChangoCore/Contracts/HTTPContracts.swift`,
   `web/src/lib/contracts.ts`, `plugins/streamdeck/src/contracts.ts`). Do not
   hand-edit these generated files; `make test` runs the generator with
   `--check` and fails on drift;
3. render it in `web/src/lib/DeckIcon.svelte` (add the name to the
   non-Phosphor `Exclude<...>` type and add a branch);
4. render it in `plugins/streamdeck/src/render.ts` (`iconSvg`);
5. if the icon should be user-selectable, add it to the
   `personalizationOptions` list in
   `macos-app/Sources/ElChangoCore/Contracts/DeckIcon+Personalization.swift`.

Use official artwork with a documented source (for example Simple Icons, CC0).
Provider icons render dynamically via `currentColor` SVG paths, so no per-icon
static asset is needed. When editing Svelte, follow the Svelte skills and run
the Svelte autofixer until clean.

## 9. Test each boundary

Put versioned fixtures under `contracts/providers/<provider>/v1/` and share them
between the Python POC and the Swift tests, as Cursor and Claude Code do. Include
an `expected-inventory.json` whose shape matches the other providers' files;
the Swift test decodes it and compares it to a snapshot mapped from the provider
(see `CodexProviderTests` / `ClaudeCodeProviderTests`). Keep timestamps explicit
in fixtures so ordering and last-activity assertions are deterministic (do not
rely on file mtime for expected values).

Add provider tests covering:

- persistent inventory and archived filtering;
- stable ID and workspace mapping;
- schema drift and ambiguous mapping failures;
- metadata cache invalidation;
- every state transition and stale-signal degradation;
- focus success, rejection, and exact verification;
- official new-session launch and encoded parameters.
- command recipe resolution, exact command-target refusal, stale preflight
  refusal, one-shot dispatch, and ambiguous-result handling when commands are
  in scope.

Extend deck and server tests to prove:

- mixed-provider sorting and pagination;
- provider-qualified IDs;
- per-session capabilities;
- dynamic new-session picker placement;
- client-scoped picker state;
- dispatch to exactly the chosen provider;
- cancel without provider action.

If adding an icon or action contract, update web and Stream Deck tests too.

## 10. Verify and document

Run `make test`, which is the full gate. It includes, and you can run
individually while iterating:

```bash
python3 scripts/validate_versions.py
python3 scripts/generate_http_contracts.py --check    # regenerate first if this fails
swift format lint --recursive --strict --configuration .swift-format \
  macos-app/Sources macos-app/Tests                   # swift format --in-place to fix
swift test --package-path macos-app
python3 scripts/validate_provider_plugins.py <provider>
npm --prefix web run lint
npm --prefix web run format:check
npm --prefix web run check
npm --prefix web test
npm --prefix web run build
npm --prefix plugins/streamdeck run check
npm --prefix plugins/streamdeck run validate
for poc in scripts/poc/<provider>/*.py; do \
  python3 -m py_compile "$poc"; python3 "$poc" --help >/dev/null; done
git diff --check
```

Also grep the diff for em-dashes (project writing rule) before finishing.

Update `docs/providers/<provider>.md` using the required structure in
[`templates/provider-doc.md`](templates/provider-doc.md). It defines all thirteen
sections and the mandatory handled-states table and state-model prose (which
state means green, terminal signal handling, stale-signal expiry, error
surfacing, and retained metadata).

Do not omit a section. Write `None established` where evidence does not yet
support content. Do not fill gaps by inference or copy a finding from another
provider. Update the roadmap only for points validated with the user. Do not
commit or push unless the user explicitly asks.
