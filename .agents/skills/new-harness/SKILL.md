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
- which capabilities are in scope: inventory, state, focus, and launch;
- which real-session experiments can run safely now;
- the expected local data roots and supported operating systems.

Treat `PROJECT.md` as brainstorming, not a requirement. Record validated
provider-specific findings in `docs/providers/<provider>.md`.

## 2. Build executable reconnaissance

Create numbered, self-documenting Python POCs under `scripts/poc/` before
building the adapter. Follow the POC standard in `AGENTS.md`.

Establish evidence for each capability separately:

1. Inventory all persistent sessions, not only live processes.
2. Identify the stable native session ID.
3. Map sessions to workspace paths.
4. Find authoritative activity signals, preferably official hooks.
5. Determine whether exact focus can be verified after acting.
6. Determine whether an official new-session mechanism exists.

Keep observations, conclusions, and hypotheses distinct. Do not infer current
state from transcript text. Do not enable focus from a shortcut or deep link
unless exact post-action identity can be verified.

## 3. Define the provider identity

Choose a stable lowercase `provider_id` without `:`. Public session IDs are
automatically qualified as `<provider_id>:<native_id>`.

Add provider presentation metadata:

```python
provider_id: ClassVar[str] = "example"
display_name: ClassVar[str] = "Example"
icon: ClassVar[ButtonIcon] = "example"
capabilities: ClassVar[frozenset[ProviderCapability]] = frozenset(
    {"focus_session", "new_session"}
)
```

Declare only capabilities proven by evidence. The new-session picker discovers
providers dynamically from `"new_session"` capability declarations.

## 4. Implement the provider boundary

Implement `AgentProvider` from `src/elchango/providers/base.py` in a dedicated
module under `src/elchango/providers/`.

Required methods:

- `snapshot() -> ProviderSnapshot`
- `focus(native_session_id) -> ProviderActionResult`
- `open_new() -> ProviderActionResult`

Use a provider-specific `ProviderError` subclass. Fail explicitly when local
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

```python
hook_recorders[provider.provider_id] = activity_store.record
```

Map signals conservatively to:

- blue: `working`;
- orange: `waiting` or rendered terminal error;
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

## 7. Register the provider

Update `src/elchango/cli.py` to:

1. add provider-specific path or launch arguments;
2. instantiate the activity store and provider;
3. run one startup snapshot to fail clearly on schema drift;
4. add the provider to the `providers` registry;
5. add its hook recorder when applicable.

Export the implementation from `src/elchango/providers/__init__.py`.

Do not add provider-specific branches to `DeckService`, the HTTP surfaces, web,
or Stream Deck. Routing must use provider metadata and capabilities.

## 8. Add the icon across contracts

If the provider needs a new icon:

1. extend `ButtonIcon` in `src/elchango/models.py`;
2. extend `DeckIconName` in `web/src/lib/contracts.ts`;
3. render it in `web/src/lib/DeckIcon.svelte`;
4. extend `DeckIconName` and parser validation in
   `streamdeck/src/contracts.ts`;
5. render it in `streamdeck/src/render.ts`.

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
PYTHONPATH=src python3 -m unittest discover -s tests
npm --prefix web run check
npm --prefix web run build
npm --prefix streamdeck run check
npm --prefix streamdeck run validate
git diff --check
```

Update `docs/providers/<provider>.md` with measured evidence, limitations,
verdicts, and official references. Update the roadmap only for points validated
with the user. Do not commit or push unless the user explicitly asks.
