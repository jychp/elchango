# Cursor provider findings

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
  exact version was not recorded in those POCs.
- Operating system: macOS; exact version was not captured.
- Global database:
  `~/Library/Application Support/Cursor/User/globalStorage/state.vscdb`.
- Workspace metadata:
  `~/Library/Application Support/Cursor/User/workspaceStorage/`.

## Evidence

The executable POCs listed below are the primary evidence. Python and Swift
inventory implementations are also checked against the same versioned fixture
under `contracts/providers/cursor/v1/`.

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

Database-only state is insufficient for the one-second live-state goal.
Aggregate `composerData` fields can remain stale throughout a turn. Individual
tool bubbles expose `loading` and `completed`, but intermediate values may be
revised when Cursor persists a completed turn. A single failed or completed
tool is therefore not terminal-turn evidence.

Fresh `hasPendingPlan` or `hasBlockingPendingActions` maps to waiting; stale
copies are ignored. Recent tool errors and completions remain working until a
terminal lifecycle event. Genuine cancellation, waiting, and error
differentiation from database data alone remain unvalidated.

Documented Cursor hooks provide low-latency event name, `conversation_id`,
`generation_id`, and terminal status. The native reporter accepts sanitized:

- `sessionStart`: idle lifecycle evidence;
- `beforeSubmitPrompt`: working;
- `stop`: done or terminal error according to status;
- `sessionEnd`: idle.

The in-memory hook overlay applies only when `conversation_id` exactly matches
a current SQLite composer ID. Cursor does not document that equality, so a
focused live test remains required. Prompt, response, tool, email, and
transcript content is discarded.

The deck maps working to blue, waiting and rendered terminal error to orange,
done to green, and idle or unknown to gray.

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
2. The POC activates Cursor, refreshes selection and recency, repeats preflight
immediately before input, and aborts if either changed. Post-action verification
detects a wrong result but cannot prevent a wrong session from briefly
receiving focus.

Production uses the Agents Window's direct `Cmd+1` through `Cmd+9` shortcuts in
pinned and repository-grouped sidebar order. Later sessions use `Cmd+9` followed
by repeated `Option+Down`. It reconstructs current sidebar order immediately
before input, sends one bounded sequence, and verifies the exact selected
composer afterward. A stale web snapshot is accepted only if the target remains
a focusable button in a fresh deck snapshot.

Cursor 3.12.17 exposes `glass.newAgentFromKeyboard` as `Cmd+N` in the Agents
Window. The launch POC activated Cursor, sent `Option+Cmd+N` to focus the Agents
Window, then sent `Cmd+N` once. `cursor/glass.selectedAgent` became `null`, but
no top-level `composerHeaders` row appeared during the five-second timeout and
settling period. The result likely represents an unpersisted blank New Agent
view. It cannot be added to the deck or verified by composer ID before the user
submits a prompt, and the action must not retry after ambiguity.

## Semantic commands

Current verdict: `SUPPORTED_WITH_VERIFIED_COMPOSER_TARGET`.

The observed Cursor composer is an enabled `AXTextArea` with exact
`AXDOMClassList` value
`tiptapProseMirrorui-prompt-input-editor__inputProseMirror-focused`. `Cmd+L`
focuses it from the conversation area. Text dispatch then atomically rechecks
the foreground Cursor identity, selected composer, enabled role, exact class,
and empty draft.

Provider mappings:

- `accept`: send `Cmd+Enter`, as explicitly validated by the operator.
  Dispatch requires the selected composer, frontmost Cursor application, and
  exact composer input. Semantic completion against a live pending approval has
  not been observed.
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

The POC defaults to dry-run, requires an explicit recipe for execution, repeats
two preflights, submits once, and never retries. `DISPATCH_SENT` proves only
verified one-shot recipe injection, not provider understanding or semantic
completion.

## Safety and target verification

- Open Cursor SQLite in strict read-only mode and validate the complete required
  schema.
- Resolve every public button again to a current provider-native composer ID.
- Reject absent, filtered, ambiguous, or unmapped identities.
- Rebuild sidebar order, selected session, and foreground evidence immediately
  before native input.
- Verify the exact selected composer after focus.
- Require the exact enabled, empty composer marker for text recipes.
- Serialize privileged actions across providers. Shortcuts target the verified
  process ID. Text and submission events use the global HID tap only after an
  atomic foreground, selected-session, and exact-input preflight because the
  observed Electron editor ignored PID-targeted Unicode events.
- Send one bounded recipe or shortcut sequence with no fallback or retry.
- Keep pagination provider-neutral and free of Cursor side effects.
- Do not treat undocumented fields, successful keystrokes, or transport
  acceptance as semantic completion.

## Degradation behavior

Missing databases, incompatible schemas, malformed records, unresolved
workspace mappings, ambiguous selection, stale preflights, and failed
Accessibility checks fail closed for Cursor. They do not block Claude Code or
prevent the native host from starting.

Stale working or waiting signals degrade to unknown rather than remaining
active indefinitely. Unmatched hook events are ignored. Database uncertainty is
rendered gray unless fresh waiting or terminal evidence supports another state.

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
- Waiting, genuine cancellation, and error differentiation remain incomplete
  without authoritative terminal hook evidence.

## POCs

- `scripts/poc/cursor/01_cursor_session_inventory.py`: strict read-only
  inventory and candidate lifecycle. Verdict:
  `EXPLOITABLE_WITH_PRECAUTIONS`.
- `scripts/poc/cursor/02_cursor_active_session.py`: exact selected-agent
  detection. Verdict: `SUPPORTED`.
- `scripts/poc/cursor/03_cursor_session_state_from_db.py`: aggregate database
  state limitations.
- `scripts/poc/cursor/04_cursor_bubble_state_from_db.py`: individual tool bubble
  transitions and provisional values.
- `scripts/poc/cursor/05_cursor_best_effort_focus.py`: recency switcher
  experiments and exact post-action verification. Verdict: `FOCUS_VERIFIED` for
  the controlled rank-2 path and an earlier two-way scenario.
- `scripts/poc/cursor/06_cursor_readonly_web_deck.py`: read-only deck snapshot
  integration.
- `scripts/poc/cursor/07_cursor_web_deck_focus.py`: deck-to-session focus
  targeting and verification.
- `scripts/poc/cursor/08_cursor_new_session.py`: one-shot blank New Agent
  launch. Verdict: `NO_NEW_COMPOSER`.
- `scripts/poc/cursor/09_cursor_command_dispatch.py`: dry-run-first,
  exact-composer, one-shot semantic command dispatch. Verdict:
  `SUPPORTED_WITH_VERIFIED_COMPOSER_TARGET`.

## References

- [Cursor hooks](https://docs.cursor.com/agent/hooks)
- Executable observations in `scripts/poc/cursor/`
- Versioned fixture in `contracts/providers/cursor/v1/`
