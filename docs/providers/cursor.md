# Cursor provider findings

| Feature | Status | Note |
| --- | --- | --- |
| Sessions inventory | ✅ supported | Strict read-only SQLite `state.vscdb`, schema-validated. |
| Session live status | ⚠️ best-effort | DB inference merged with lifecycle hooks; live hook ID correlation still needs a focused test. |
| Session focus | ✅ supported | Agents Window `Cmd+1`..`Cmd+9` sidebar shortcuts with exact selected-composer verification. |
| Session creation | ⚠️ best-effort | `Cmd+N` opens an unpersisted blank New Agent view (`NO_NEW_COMPOSER`). |
| Commands | ✅ supported | Verified composer accessibility marker; acts on the frontmost Cursor window. |

Status legend: `✅ supported`, `⚠️ best-effort`, `❌ not supported`.

## Scope and status

This provider targets local Cursor agent sessions on macOS. The evidence was
collected from undocumented local storage, documented lifecycle hooks, native
keyboard behavior, and macOS Accessibility. Undocumented integration points
must be revalidated when Cursor changes.

The native Swift provider implements strict read-only inventory, workspace
mapping, exact selected-agent detection, hook-backed state, verified focus,
blank New Agent launch, and four semantic commands.

Current conservative verdicts:

- Inventory: `EXPLOITABLE_WITH_PRECAUTIONS`.
- Selected session: `SUPPORTED`.
- Live state: hook-backed implementation exists, but hook ID correlation still
  requires a focused live test.
- Existing-session focus: verified for observed native shortcut paths.
- New session: `NO_NEW_COMPOSER`; the shortcut opens an unpersisted blank view.
- Commands: `SUPPORTED_WITH_VERIFIED_COMPOSER_TARGET`.

## Tested versions and environment

- Observation dates: July 16 and July 17, 2026.
- Cursor version captured for New Agent testing: 3.12.17.
- Cursor version for other tests: the installation observed on July 16, 2026;
  exact version was not recorded in those observations.
- Operating system: macOS; exact version was not captured.
- Global database:
  `~/Library/Application Support/Cursor/User/globalStorage/state.vscdb`.
- Workspace metadata:
  `~/Library/Application Support/Cursor/User/workspaceStorage/`.

## Evidence

The observations recorded here are the primary evidence. The Swift inventory
implementation is checked against the versioned fixture under
`contracts/providers/cursor/v1/`.

A 90-second inventory observation at 0.2-second intervals covered switching
away and back, creating and interacting with a session, and closing or
archiving it. It observed visibility changes, new-session creation, a stable
composer ID through that lifecycle, removal from the candidate set after
closure or archival, and changes within polling resolution. It did not test
restart or upgrade stability.

A separate 90-second selected-session test matched three user-driven switches
across workspaces. Opening a canvas did not change the selected agent ID.
Controlled database-state tests observed running tool bubbles and their later
completion, but also showed aggregate state remaining stale during active work.

Focus experiments rejected an ineffective composer deep link and unsafe
`cursor --reuse-window` behavior, then established verified native shortcut
paths. Command experiments observed a harmless text submission and successful
`/summarize` dispatch under exact composer checks.

## Inventory and identity

SQLite is opened with `SQLITE_OPEN_READONLY`, `PRAGMA query_only=ON`, disabled
trusted schema features, and one read transaction. It reads current WAL-backed
updates without copying the database or stopping Cursor. Missing required
tables or columns fail explicitly.

Observed structures:

- `composerHeaders`: composer IDs, workspace IDs, timestamps, archive state,
  subagent state, header metadata, and recency.
- `ItemTable`: additional UI state, including visibility and selected-agent
  keys.
- `composerData:<composer-id>`: aggregate composer data that can be stale.
- `bubbleId:<composer-id>:<bubble-id>`: individual tool bubble statuses.

A candidate user session:

- is not archived;
- is not a draft;
- is not ephemeral;
- is not a subagent;
- has an observed `lastUpdatedAt`.

This heuristic excludes stale empty-window and ephemeral records, but it is not
a proven definition of an open agent tab. `isArchived` is likewise not proven
equivalent to tab closure, although the observed archived session left the
candidate set.

The stable native identity is `composerHeaders.composerId`. Public surfaces
receive a provider-qualified ID. Several old sessions may retain
`visible=true`, and a correctly selected session may have `visible=false`, so
visibility is auxiliary and never identity.

`cursor/glass.selectedAgent` in `ItemTable` contains the selected composer ID.
It is accepted only when it resolves to exactly one emitted, mapped candidate.
Absent, unresolved, filtered, duplicated, or unmapped values yield unknown
selection. The selected agent remains selected while a canvas, diff, browser,
terminal, or file is foreground content; selection does not imply keyboard
focus.

## Workspace mapping

`workspaceStorage/*/workspace.json` maps workspace hashes to local paths.
Composer header JSON may also contain a workspace or worktree path directly.
The provider emits a selected session only when its composer ID uniquely maps to
one candidate with a workspace path.

Visibility timestamps and header `lastUpdatedAt` represent different activity:
visibility metadata can change while the header timestamp remains unchanged.
Neither is substituted for workspace identity or selected-session evidence.

## State model and hooks

State is the shared `SessionState` enum (`idle`, `working`, `waiting`, `done`,
`error`, `unknown`) and is derived from two merged sources: database inference
and lifecycle hooks. The handled states are:

| State | Deck color | Produced by |
| --- | --- | --- |
| `working` | blue | hook progress (`beforeSubmitPrompt`, `preCompact`, thought/response, subagent activity); fresh database generation, running tool, or `generating`/`running`/`pending` status |
| `waiting` | orange | fresh `hasPendingPlan` or `hasBlockingPendingActions` |
| `done` | green | hook `stop` (see status handling below) |
| `error` | red | hook `stop` with `error`/`aborted` status; fresh or stale composer-level `error`/`failed` status |
| `idle` | gray | `sessionStart`/`sessionEnd`; stale non-terminal database signals; empty composer data |
| `unknown` | gray | never emitted directly; a stale hook signal is removed so database inference governs again |

Only the hook `stop` path can produce `done` (green); database inference alone
never emits `done`. Merge rule: a hook `done` or `error` always overrides the
database state; otherwise the hook state is applied unless the database inferred
`waiting`.

Database-only state is insufficient for the one-second live-state goal.
Aggregate `composerData` fields can remain stale throughout a turn. Individual
tool bubbles expose `loading` and `completed`, but intermediate values may be
revised when Cursor persists a completed turn. A single failed or completed
tool is therefore not terminal-turn evidence.

Fresh `hasPendingPlan` or `hasBlockingPendingActions` maps to waiting; stale
copies are ignored. Recent tool errors and completions remain working until a
terminal lifecycle event, because a single failed tool may be retried within
the same turn. A composer-level `error` or `failed` status is different: it
maps to a rendered terminal `error` (red) so a model or turn failure surfaces
on the deck as its own color instead of appearing as a generic waiting or idle
tile. Genuine cancellation and waiting differentiation from database data alone
remain unvalidated.

Documented Cursor hooks provide low-latency event name, `conversation_id`,
`generation_id`, and terminal status. The native reporter accepts only bounded
lifecycle metadata:

- `sessionStart`: idle lifecycle evidence;
- `beforeSubmitPrompt`: starts a working generation;
- `preCompact`, `afterAgentThought`, and `afterAgentResponse`: working progress;
- `subagentStart` and `subagentStop`: bounded parallel-child progress;
- parent `stop`: done or terminal error according to status;
- `sessionEnd`: idle.

Terminal status handling is tolerant: `completed` maps to done and `error` or
`aborted` map to a terminal error, all at observed confidence. A `stop` whose
status is missing or unrecognized is still treated as a terminal completion, at
reduced (candidate) confidence and with the raw status preserved in the detail,
rather than being rejected and dropped. This prevents a completed session from
remaining stuck in working when Cursor omits or changes the stop status.

Turns are keyed by conversation and generation. Events from an older
generation cannot terminate the current turn. A parent stop received while
subagents remain active is deferred until the final child stops. A failed or
aborted child makes the deferred terminal result an error. Renewed parent
progress clears a deferred result and requires a later authoritative stop.

Hook receipt never reads the database. It stores sanitized observations, then
the next snapshot applies them only when `conversation_id` exactly matches a
current SQLite composer ID. This permits hooks to arrive before persistence
without weakening identity matching. Cursor does not document that equality,
so a focused live test remains required. Prompt, thought, response, tool,
summary, email, and transcript content is discarded.

Green completion survives passive native selection changes until an explicit
elChango focus action acknowledges the session. A new lifecycle event,
`sessionEnd`, or the bounded one-hour hook TTL may also clear it. Fresh
persisted `hasPendingPlan` or `hasBlockingPendingActions` remains the
conservative waiting signal; plan-mode text and intermediate reasoning are not
inferred as idle or waiting.

A non-terminal hook signal (`working` or `waiting`) is expired after a bounded
terminal deadline (ten minutes) when the expected terminal event never arrives.
The stale observation is removed rather than retained, so database inference
governs the tile again instead of leaving it stuck in working or waiting until
the one-hour TTL. Terminal `done` and `error` observations are not subject to
this shorter deadline.

The deck maps working to blue, waiting to orange, rendered terminal error to
red, done to green, and idle or unknown to gray.

## Focus and launch

Cursor exposes no supported deep link for an existing local agent by composer
ID. The tested undocumented `/agent?composerId=...` route had no effect.
`cursor --reuse-window` is unsafe because it can offer to cancel running agents
before replacing the workspace.

An early Control+Tab experiment used `composerHeaders.recency`. Synthetic input
was inconsistent when key-down and key-up events were too fast: two Tab events
toward rank 2 selected rank 1, three later selected rank 6, and the full recency
snapshot remained unchanged. A physical Control+Tab selected rank 1. With
100 ms key presses and pauses, two synthetic presses selected and verified rank
2. The focus routine activates Cursor, refreshes selection and recency, repeats
preflight immediately before input, and aborts if either changed. Post-action
verification
detects a wrong result but cannot prevent a wrong session from briefly
receiving focus.

Production uses the Agents Window's direct `Cmd+1` through `Cmd+9` shortcuts in
pinned and repository-grouped sidebar order. Later sessions use `Cmd+9` followed
by repeated `Option+Down`. It reconstructs current sidebar order immediately
before input, sends one bounded sequence, and verifies the exact selected
composer afterward. A stale web snapshot is accepted only if the target remains
a focusable button in a fresh deck snapshot.

Cursor 3.12.17 exposes `glass.newAgentFromKeyboard` as `Cmd+N` in the Agents
Window. The launch experiment activated Cursor, sent `Option+Cmd+N` to focus the
Agents Window, then sent `Cmd+N` once. `cursor/glass.selectedAgent` became
`null`, but
no top-level `composerHeaders` row appeared during the five-second timeout and
settling period. The result likely represents an unpersisted blank New Agent
view. It cannot be added to the deck or verified by composer ID before the user
submits a prompt, and the action must not retry after ambiguity.

## Semantic commands

Current verdict: `SUPPORTED_WITH_VERIFIED_COMPOSER_TARGET`.

The observed Cursor composer is an enabled `AXTextArea` with exact
`AXDOMClassList` value
`tiptapProseMirrorui-prompt-input-editor__inputProseMirror-focused`. `Cmd+L`
focuses it from the conversation area when it is not already focused. Text
dispatch follows the shared
[native text command dispatch contract](../command-dispatch.md): activate
Cursor, verify the foreground process, enabled role, and exact class, then
capture, replace, submit, and restore any existing draft. The command acts on
whatever session the frontmost Cursor window has on screen; elChango no longer
verifies which composer is selected.

Provider mappings:

- `accept`: send `Cmd+Enter`, as explicitly validated by the operator.
  Dispatch requires the frontmost Cursor application and exact composer input; it
  acts on the active window without verifying the selected composer. Semantic
  completion against a live pending approval has not been observed.
- `create_pr`: submit `Open a pull request for the current branch.` as an agent
  instruction.
- `commit_push`: submit `Commit the current changes with a Conventional Commit
  message and push the current branch.` as an agent instruction.
- `compact`: submit `/summarize` with two delayed Return presses, one to select
  the suggestion and one to submit it.

A harmless `test` instruction arrived after one Return with a 500 ms delay.
The `/summarize` sequence triggered summarization successfully. The timing is
operator-approved but does not semantically identify the autocomplete
suggestion.

Dispatch requires an explicit recipe, repeats two preflights, submits once, and
never retries. `DISPATCH_SENT` proves only verified one-shot recipe injection,
not provider understanding or semantic completion.

## Safety and target verification

- Open Cursor SQLite in strict read-only mode and validate the complete required
  schema.
- Resolve every public button again to a current provider-native composer ID.
- Reject absent, filtered, ambiguous, or unmapped identities.
- Focus rebuilds sidebar order, selected session, and foreground evidence
  immediately before native input, and verifies the exact selected composer
  after acting. Commands require only that Cursor is the frontmost application
  and act on the active window (no selected-composer verification).
- Require the exact enabled composer marker and bounded draft capture for text
  recipes.
- Serialize privileged actions across providers. Shortcuts target the verified
  process ID. Text and submission events use the global HID tap only after an
  atomic foreground and exact-input preflight because the observed Electron
  editor ignored PID-targeted Unicode events.
- Send one bounded recipe or shortcut sequence with no fallback or retry.
- Keep pagination provider-neutral and free of Cursor side effects.
- Do not treat undocumented fields, successful keystrokes, or transport
  acceptance as semantic completion.

## Degradation behavior

Missing databases, incompatible schemas, malformed records, unresolved
workspace mappings, ambiguous selection, stale preflights, and failed
Accessibility checks fail closed for Cursor. They do not block Claude Code or
prevent the native host from starting.

Expired working or waiting hook signals are removed rather than remaining
active indefinitely. The session then falls back to its persisted database
state, which is currently idle and rendered gray unless fresh waiting or
terminal evidence supports another state. Unmatched hook events are ignored.

## Limitations and open questions

- Do composer IDs survive Cursor restarts?
- Which events change `lastUpdatedAt`, visibility timestamps, or both?
- Can an open session be distinguished reliably from an unarchived historical
  session?
- Does every hook `conversation_id` equal its SQLite `composerId`?
- How does the schema behave across Cursor upgrades?
- Does `composerHeaders.recency` continue to match the switcher across larger
  and mixed local or cloud session sets?
- Does the persisted sidebar order remain stable across Cursor versions?
- Does the observed composer Accessibility marker remain stable?
- Can `accept` be observed against a real pending approval without ambiguity?
- Waiting remains limited to fresh explicit pending-plan or blocking-action
  evidence. Cursor exposes no general authoritative "needs user input" hook.
- Live behavior still needs manual confirmation for automatic and manual
  compaction plus foreground and background parallel subagents.

## References

- [Cursor hooks](https://docs.cursor.com/agent/hooks)
- Versioned fixture in `contracts/providers/cursor/v1/`
