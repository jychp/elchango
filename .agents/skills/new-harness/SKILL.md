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
and expose a loopback hook recorder through:

Implement `recordHook(_:observedAtMilliseconds:)` on the provider and route it
through the existing provider registry.

Map signals conservatively to:

- blue: `working`;
- orange: `waiting`;
- red: rendered terminal `error`;
- green: `done`;
- gray: `idle` or unknown.

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

Add the implementation to the `ElChangoProviders` SwiftPM target.

Do not add provider-specific branches to `DeckService`, the HTTP surfaces, web,
or Stream Deck. Routing must use provider metadata and capabilities.

## 8. Add the icon across contracts

If the provider needs a new icon:

1. extend `DeckIcon` in
   `macos-app/Sources/ElChangoCore/Contracts/DeckContracts.swift`;
2. extend `DeckIconName` in `web/src/lib/contracts.ts`;
3. render it in `web/src/lib/DeckIcon.svelte`;
4. extend `DeckIconName` and parser validation in
   `plugins/streamdeck/src/contracts.ts`;
5. render it in `plugins/streamdeck/src/render.ts`.

Use official artwork with a documented source. When editing Svelte, follow the
Svelte skills and run the Svelte autofixer until clean.

## 9. Test each boundary

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

Run:

```bash
swift test --package-path macos-app
npm --prefix web run check
npm --prefix web run build
npm --prefix plugins/streamdeck run check
npm --prefix plugins/streamdeck run validate
git diff --check
```

Update `docs/providers/<provider>.md` using the required structure in
[`templates/provider-doc.md`](templates/provider-doc.md). It defines all thirteen
sections and the mandatory handled-states table and state-model prose (which
state means green, terminal signal handling, stale-signal expiry, error
surfacing, and retained metadata).

Do not omit a section. Write `None established` where evidence does not yet
support content. Do not fill gaps by inference or copy a finding from another
provider. Update the roadmap only for points validated with the user. Do not
commit or push unless the user explicitly asks.
