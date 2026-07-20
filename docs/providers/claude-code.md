# Claude Code provider findings

| Feature | Status | Note |
| --- | --- | --- |
| Sessions inventory | ✅ supported | Bounded metadata parse of `local_*.json` with 1:1 transcript correlation. |
| Session live status | ✅ supported | Official hooks; idle when no hook signal. |
| Session focus | ✅ supported | Sidebar shortcut from persisted config order, with exact post-action verification. |
| Session creation | ✅ supported | Documented `claude://code/new` neutral deep link. |
| Commands | ✅ supported | Composer marker with `AXGroup` fallback exception; acts on the frontmost Claude window. |

Status legend: `✅ supported`, `⚠️ partial` (implemented with a behavioral limitation), `❌ not supported`.

## Scope and status

This provider targets Claude Code sessions opened by Claude Desktop on macOS.
Claude Cowork and ordinary Claude chats are outside its scope.

The native Swift provider implements bounded metadata inventory, exact
transcript correlation, unique `lastFocusedAt` selection, official hook state,
verified sidebar focus, the documented `claude://code/new` launch, and the four
shared semantic commands. Malformed Claude records degrade only this provider.

Current behavior:

- Inventory: bounded metadata parse with exact transcript correlation.
- State: driven by official Claude Code hooks; idle when no hook signal.
- Existing-session focus: native sidebar shortcut with exact post-action
  verification.
- New session: the documented neutral `claude://code/new` deep link.
- Commands: dispatched against the composer accessibility marker (with the
  `AXGroup` fallback exception) on the frontmost window.

## Tested versions and environment

- Observation date: July 17, 2026.
- Operating system: macOS; exact version was not captured.
- Claude Desktop version: not captured.
- Claude Code version: not captured.
- Inventory root:
  `~/Library/Application Support/Claude/claude-code-sessions`.
- Transcript root: `~/.claude/projects/`.
- Process registry: `~/.claude/sessions`.

## Evidence

The observations recorded here provide the primary evidence. The Swift
implementation shares versioned fixtures under
`contracts/providers/claude-code/v1/`.

The inventory observation covered 711 persistent Desktop Code records, including
17 non-archived sessions that matched the Claude Desktop session list. Five of
those 17 had a live Claude Code process. Every non-archived record had unique
Desktop and CLI IDs, exact workspace fields, timestamps, archive state, and a
matching top-level transcript.

The hook path parses the official Claude Code hook events, sanitizes the
payload, and maps each event to a provider-neutral state. The focus path rejects
unverified deep links and drives native sidebar positions 3 and 10. The command
path handles a text submission, a `/compact` dispatch, and a `Cmd+Enter` plan
acceptance under the documented target checks.

Fixture measurements on July 17, 2026:

- first snapshot of 711 records: 126.2 ms;
- cached snapshot: 12.4 ms;
- emitted non-archived inventory: 17 sessions.

## Inventory and identity

Desktop `sessionId` is the persistent UI identity. `cliSessionId` is the Claude
Code runtime, transcript, and expected hook identity. The observed record shape
was:

```json
{
  "sessionId": "local_74fa6191-71b8-4a05-9409-bdb618a7e65d",
  "cliSessionId": "daff91a1-3ad2-4c5f-b527-38f2bc29a186",
  "cwd": "/path/to/worktree",
  "originCwd": "/path/to/repository",
  "createdAt": 1784329817258,
  "lastActivityAt": 1784335060070,
  "isArchived": false,
  "title": "Session title"
}
```

Desktop records can contain large conversation payloads. Inventory uses a
bounded top-level metadata parser and stops before those payloads. It excludes
archived records, caches unchanged metadata by file modification time and size,
and sorts sessions by `lastActivityAt`.

Transcripts are correlated only by exact CLI session ID. The separate process
registry annotates process liveness but never filters inventory. Using it as the
inventory source would have omitted 12 of the 17 visible sessions in the
observed snapshot.

Selection requires one uniquely newest `lastFocusedAt` value. Commands remain
disabled until an inventory snapshot uniquely confirms the selected session.

## Workspace mapping

Every observed non-archived record supplied:

- `cwd`, the current worktree or working directory;
- `originCwd`, the originating repository workspace;
- `lastActivityAt`, the provider-neutral ordering timestamp.

The provider uses these direct fields instead of transcript content or process
state. Missing or malformed identity and workspace metadata fails that record
conservatively.

## State model and hooks

State comes from official Claude Code hooks, not transcript inference. Relevant
official payloads share `session_id`, `transcript_path`, `cwd`, and
`hook_event_name`. The transcript is asynchronous and may lag the current hook.
Native HTTP handlers are preferred because they avoid Claude's shell hook
executor.

State is the shared `SessionState` enum (`idle`, `working`, `waiting`, `done`,
`error`, `unknown`). Unlike Cursor there is no database inference; all live
state is hook-driven. The handled states are:

| State | Deck color | Produced by |
| --- | --- | --- |
| `working` | blue | `UserPromptSubmit`, tool/permission progress, subagent activity, compaction, `SessionStart(source: "compact")`, and `Stop` while background tasks remain |
| `waiting` | orange | `PermissionRequest`, `Elicitation`, `PreToolUse` for `AskUserQuestion`/`ExitPlanMode`, and `permission_prompt`/`elicitation_dialog`/`agent_needs_input` notifications |
| `done` | green | `Stop` with an empty background-task registry, and the `idle_prompt` notification |
| `error` | red | `StopFailure` |
| `idle` | gray | `SessionStart` and `SessionEnd` |
| `unknown` | gray | a `working`/`waiting` signal older than the ten-minute terminal deadline, when no terminal event arrived |

The implemented event mapping is:

- `UserPromptSubmit`: blue, working, keyed by `(session_id, prompt_id)` when the
  current Claude Code version supplies `prompt_id`;
- `PreToolUse` for `AskUserQuestion` or `ExitPlanMode`: orange, waiting;
- corresponding `PostToolUse`, `PostToolBatch`, and `PermissionDenied`: blue,
  working or retry progress;
- `PermissionRequest`: orange, waiting for tool approval;
- `Elicitation`: orange, waiting for MCP input;
- `ElicitationResult`, `elicitation_complete`, and `elicitation_response`: blue,
  working after input;
- `permission_prompt`, `elicitation_dialog`, and `agent_needs_input`: orange,
  waiting;
- `idle_prompt`: green, done;
- `agent_completed`: blue candidate progress only because it does not identify
  the whole parent turn;
- `SubagentStart` and `SubagentStop`: bounded blue child progress;
- `PreCompact` and `PostCompact` for manual or automatic compaction: blue,
  working;
- `SessionStart(source: "compact")`: blue resumed progress rather than idle;
- `Stop`: green only when its authoritative `background_tasks` array is empty;
- `StopFailure`: error, rendered red by the deck;
- `SessionStart` and `SessionEnd`: gray, idle.

When `Stop.background_tasks` is non-empty, the session remains blue. Background
progress may resume the parent, and only a later `Stop` with an empty registry
terminates the turn. Older Claude Code versions without the registry fall back
to tracked subagent IDs conservatively. Hook receipt does not rebuild inventory:
the next snapshot applies a sanitized observation only to an exact
`cliSessionId` match.

The activity store retains the session ID, current prompt ID, active subagent
IDs, and the latest event, state, timestamp, confidence, and bounded detail.
Permission mode, session source, compaction trigger, and bounded background-task
metadata are validated and used transiently but are not retained. It retains no
prompt, assistant, notification message, summary, command, tool input/output,
or transcript content. Green completion survives passive selection changes
until explicit elChango focus acknowledgement, a new lifecycle event, session
end, or the bounded one-hour hook TTL. Hook signals are correlated to a session
by matching the hook `session_id` to the Desktop record's `cliSessionId`.

## Focus and launch

The documented deep link for a new Code session is
`claude://code/new`. elChango opens it without a folder parameter so Claude
Desktop presents its neutral new Code session screen. Provider selection is
client-scoped on both web and Stream Deck.

No official existing-session deep link was found. Tests with both the Desktop
`sessionId` and Claude Code `cliSessionId` brought Claude frontmost but did not
select the target or change its `lastFocusedAt`; the verdict was
`FOCUS_NOT_VERIFIED`.

Native shortcuts provided the verifiable focus mechanism:

- `Cmd+1` through `Cmd+9` select corresponding persisted sidebar sessions.
- Current order comes from `claude_desktop_config.json` and mirrors the rendered
  sidebar top to bottom:
  1. **Ungrouped pinned sessions** first: those in `pinnedOrder` in that order,
     then any remaining `starred-local-code-sessions` (also pinned but without a
     drag position) by descending `lastActivityAt`.
  2. **Each custom group** in `dframe-group-scopes.groups` order, showing that
     group's `order`. A pinned session that is also assigned to a group stays in
     its group at its group position; pinning does not pull it to the top.
  3. **Ungrouped, unpinned sessions** last, by descending `lastActivityAt`.
- **Collapsed groups** (keys in `epitaxy-tasks-store.state.collapsedGroups`) hide
  their rows, so their sessions are omitted from the shortcut order and are not
  focus-capable while collapsed.
- Legacy installations use ungrouped `starred-local-code-sessions` in reverse
  persisted order, then sessions in `customGroupOrder`, then remaining sessions
  by descending `lastActivityAt`.
- Positions after 9 use `Cmd+9`, followed by one `Ctrl+Tab` for each additional
  position.
- Positions 3 and 10 independently returned `FOCUS_VERIFIED`.

Production reconstructs this order immediately before native keyboard dispatch
from either the legacy grouped fields or the current qualified pinned, scoped
group, and Ungrouped fields. The current section and group ordering was
validated against the visible Claude sidebar on July 19, 2026, without
dispatching a shortcut. Production activates Claude before resolving the
shortcut and rechecks the order, selected session, and foreground application.
Claude may persist
`lastFocusedAt` several seconds after its UI changes, so focus dispatch does not
wait for that delayed record. Any missing order entry, stale order, or failed
preflight rejects the action. Commands stay disabled until a later inventory
snapshot uniquely confirms the selected session.

If the target is already the uniquely most recently focused session, elChango
activates Claude without navigation and verifies that the same target remains
uniquely selected. Focus does not submit prompts or change conversation content.

## Semantic commands

Current verdict: `SUPPORTED_WITH_VERIFIED_COMPOSER_TARGET`.

The observed Claude composer is an enabled `AXTextArea` with description
`Prompt` and exact `AXDOMClassList` value
`tiptapProseMirrorProseMirror-focused`. Product text dispatch follows the shared
[native text command dispatch contract](../command-dispatch.md): activate
Claude, verify the foreground process, enabled input role, and exact marker, then
capture, replace, submit, and restore any existing draft. The command acts on
whatever session the frontmost Claude window has on screen; elChango no longer
verifies which session is selected.

Provider mappings:

- `accept`: send `Cmd+Enter` once. This was observed accepting a real open plan.
  It is an application-level shortcut and intentionally does not require
  composer focus.
- `create_pr`: submit `Open a pull request for the current branch.` as an agent
  instruction with two delayed Return presses.
- `commit_push`: submit `Commit the current changes with a Conventional Commit
  message and push the current branch.` as an agent instruction with two
  delayed Return presses.
- `compact`: submit `/compact` with two delayed Return presses, one to select
  the suggestion and one to submit it.

A harmless `test` instruction remained displayed after the first Return and was
submitted by the second. The `/compact` sequence also triggered compaction with
two Returns. The implementation preserves this operator-approved timing but
does not claim to identify the intermediate suggestion state semantically.

`DISPATCH_SENT` proves only that one recipe was injected while Claude remained
the frontmost application. It does not prove the command hit a specific session,
nor that Claude understood or completed the semantic operation.

## Safety and target verification

- Persistent metadata is parsed with bounded reads; conversation payloads are
  not consumed.
- Inventory, transcript, hook, and process identities are correlated only by
  exact IDs.
- Provider actions share one serialized native automation boundary with Cursor.
- Focus rechecks the exact selected session and frontmost bundle. Command
  dispatch rechecks only the frontmost bundle: it acts on whatever session the
  active window shows and does not verify the selected session.
- Text dispatch additionally requires the enabled provider-specific
  Accessibility target and bounded draft capture when the input is focused.
  If Accessibility successfully reports the observed non-text `AXGroup` role,
  elChango uses the documented best-effort exception. Exact application and
  session verification remain, but input verification, draft capture, deletion,
  and restoration are skipped. Claude then routes application-level typing to
  its prompt. Other roles, input mismatches, disabled inputs, and Accessibility
  read failures reject the action without typing.
- Shortcuts target the verified process. Text and submission events use the
  global HID tap because the observed Electron editor ignored PID-targeted
  Unicode events. The verified path requires foreground and exact-input
  preflight; the best-effort exception omits only the input preflight. Each
  recipe is sent at most once, with no automatic retry.
- Existing-session focus requires exact preflight and bounded shortcut
  verification. Commands, by contrast, are eligible whenever Claude is the
  frontmost application, whether the session was focused from the deck or by
  hand; the user is responsible for having the intended session in front.
- Surface requests carry semantic IDs, not arbitrary recipe text.

## Degradation behavior

Malformed records, absent metadata, ambiguous selection, missing sidebar order,
failed Accessibility checks, and stale preflights reject the affected action.
They do not disable Cursor or prevent the host from starting.

Sessions without fresh hook evidence are gray with persisted confidence. A
working or waiting signal older than ten minutes without a terminal event
becomes unknown with explicit degraded detail. Hooks may arrive before Desktop
persists a matching record; bounded observations remain inert until an exact
later inventory match.

## Limitations and open questions

- Claude Desktop and Claude Code store IDs (`sessionId`, `cliSessionId`) in an
  undocumented format; their stability across resume is not guaranteed by the
  harness and must be revalidated when it changes.
- The sidebar configuration and the composer Accessibility markers are
  undocumented and may change across Claude Desktop versions.
- Exact Claude Desktop and Claude Code versions were not captured.
- A successful dispatch reports only that the recipe was injected on the
  frontmost window; the provider never asserts that the command semantically
  completed.

## References

- [Claude Code hooks reference](https://docs.anthropic.com/en/docs/claude-code/hooks)
- [Claude Code common workflows](https://docs.anthropic.com/en/docs/claude-code/common-workflows)
- [Open Claude Desktop with a link](https://support.claude.com/en/articles/14729294-open-claude-desktop-with-a-link)
