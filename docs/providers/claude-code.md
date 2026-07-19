# Claude Code provider findings

## Scope and status

This provider targets Claude Code sessions opened by Claude Desktop on macOS.
Claude Cowork and ordinary Claude chats are outside its scope.

The native Swift provider implements bounded metadata inventory, exact
transcript correlation, unique `lastFocusedAt` selection, official hook state,
verified sidebar focus, the documented `claude://code/new` launch, and the four
shared semantic commands. Malformed Claude records degrade only this provider.

Current conservative verdicts:

- Inventory: `INVENTORY_SUPPORTED` for the observed installation.
- State: implemented from official hooks, but live hook observation remains
  outstanding.
- Existing-session focus: `FOCUS_VERIFIED` for independently tested sidebar
  positions 3 and 10.
- New session: supported through the documented neutral Code deep link.
- Commands: `SUPPORTED_WITH_VERIFIED_COMPOSER_TARGET`.

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

The executable POCs listed below provide the primary evidence. The Python and
Swift implementations also share versioned fixtures under
`contracts/providers/claude-code/v1/`.

The inventory POC observed 711 persistent Desktop Code records, including 17
non-archived sessions that matched the Claude Desktop session list. Five of
those 17 had a live Claude Code process. Every non-archived record had unique
Desktop and CLI IDs, exact workspace fields, timestamps, archive state, and a
matching top-level transcript.

The hook probe generated a non-installed configuration and passed its synthetic
self-check. It recorded no live hook events because testing was deferred to
avoid disturbing ongoing sessions. The focus probe rejected unverified deep
links, then independently verified native sidebar positions 3 and 10. The
command probe observed one harmless text submission, successful `/compact`
dispatch, and a real `Cmd+Enter` plan acceptance under the documented target
checks.

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

The implemented mapping is:

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
- `StopFailure`: error, rendered orange by the four-color deck;
- `SessionStart` and `SessionEnd`: gray, idle.

When `Stop.background_tasks` is non-empty, the session remains blue. Background
progress may resume the parent, and only a later `Stop` with an empty registry
terminates the turn. Older Claude Code versions without the registry fall back
to tracked subagent IDs conservatively. Hook receipt does not rebuild inventory:
the next snapshot applies a sanitized observation only to an exact
`cliSessionId` match.

The activity store retains IDs, event types, status, prompt ID, permission mode,
session source, compaction trigger, and bounded background task
ID/type/status/agent-type metadata. It
retains no prompt, assistant, notification message, summary, command, tool
input/output, or transcript content. Green completion persists until an
explicit elChango focus action acknowledges it. Live evidence must still confirm
that hook `session_id` equals the Desktop record's `cliSessionId` and that the
expected event sequences reliably represent turns and waiting states.

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
- Order comes from `claude_desktop_config.json`: ungrouped
  `starred-local-code-sessions` in reverse persisted order, then sessions in
  `customGroupOrder`, then non-starred sessions by descending
  `lastActivityAt`.
- Positions after 9 use `Cmd+9`, followed by one `Ctrl+Tab` for each additional
  position.
- Positions 3 and 10 independently returned `FOCUS_VERIFIED`.

Production reconstructs this order immediately before native keyboard dispatch
and rechecks the order, selected session, and foreground application. Claude may
persist `lastFocusedAt` several seconds after its UI changes, so that delayed
value is not used for immediate surface feedback. Any missing order entry or
failed preflight rejects the action. For a different target, success means the
exact sidebar shortcut was dispatched and Claude remained frontmost, not that
the delayed selected-session record already confirms the target. Commands stay
disabled until a later inventory snapshot uniquely selects that session.

If the target is already the uniquely most recently focused session, elChango
activates Claude without navigation and verifies that the same target remains
uniquely selected. Focus does not submit prompts or change conversation content.

## Semantic commands

Current verdict: `SUPPORTED_WITH_VERIFIED_COMPOSER_TARGET`.

The observed Claude composer is an enabled `AXTextArea` with description
`Prompt` and exact `AXDOMClassList` value
`tiptapProseMirrorProseMirror-focused`. Text recipes require that exact marker
and an empty draft. The POC and product scripts perform two preflights and
recheck the frontmost bundle, selected target, enabled input role, marker, and
draft immediately before dispatch.

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

`DISPATCH_SENT` proves only that one recipe was injected while the exact Desktop
target remained uniquely selected immediately afterward. It does not prove
Claude understood or completed the semantic operation.

## Safety and target verification

- Persistent metadata is parsed with bounded reads; conversation payloads are
  not consumed.
- Inventory, transcript, hook, and process identities are correlated only by
  exact IDs.
- Provider actions share one serialized native automation boundary with Cursor.
- Every privileged action rechecks the exact selected session and frontmost
  bundle immediately before dispatch.
- Text dispatch additionally requires the enabled, empty, provider-specific
  Accessibility target.
- Shortcuts target the verified process. Text and submission events use the
  global HID tap only after an atomic foreground, selected-session, and
  exact-input preflight because the observed Electron editor ignored
  PID-targeted Unicode events. Each recipe is sent at most once, with no
  automatic retry.
- Existing-session focus requires exact preflight and bounded shortcut
  verification. A newly focused Claude target is not considered selected for
  command eligibility until later inventory uniquely confirms it.
- Surface requests carry semantic IDs, not arbitrary recipe text.

## Degradation behavior

Malformed records, absent metadata, ambiguous selection, missing sidebar order,
failed Accessibility checks, and stale preflights reject the affected action.
They do not disable Cursor or prevent the host from starting.

Sessions without fresh hook evidence are gray with persisted confidence. A
working or waiting signal older than ten minutes without a terminal event
becomes unknown with explicit degraded detail. Hooks may arrive before Desktop
persists a matching record; bounded observations remain inert until an exact
later inventory match. This conservative timeout is subject to revision after
live hook testing.

## Limitations and open questions

- Live hooks have not yet confirmed that `session_id` equals Desktop
  `cliSessionId`.
- Persistent record creation and update latency remains unmeasured.
- Stability of Desktop `sessionId` and `cliSessionId` across resume remains
  unproven.
- Live evidence is still needed for blue-to-green turn transitions, orange
  waiting transitions and clearing, native HTTP reachability, compaction,
  foreground and background subagents, and background-task wakeups.
- Exact Claude Desktop and Claude Code versions were not captured.
- Accessibility markers and undocumented sidebar configuration may change.
- Semantic completion is not proven by successful dispatch.

## POCs

- `scripts/poc/claude/01_claude_code_session_inventory.py`: bounded persistent
  inventory, IDs, workspace fields, transcripts, and process annotation.
  Verdict: `INVENTORY_SUPPORTED` for the observed installation.
- `scripts/poc/claude/02_claude_code_hook_probe.py`: generated hook
  configuration, payload sanitization, and synthetic analysis. Verdict:
  `UNPROVEN_REQUIRES_LIVE_HOOK_OBSERVATION`.
- `scripts/poc/claude/03_claude_desktop_focus.py`: deep-link rejection and
  verified native sidebar focus. Verdict: `FOCUS_VERIFIED` for tested positions
  3 and 10.
- `scripts/poc/claude/04_claude_command_dispatch.py`: dry-run-first,
  exact-target, one-shot command dispatch. Verdict:
  `SUPPORTED_WITH_VERIFIED_COMPOSER_TARGET`.

## References

- [Claude Code hooks reference](https://docs.anthropic.com/en/docs/claude-code/hooks)
- [Claude Code common workflows](https://docs.anthropic.com/en/docs/claude-code/common-workflows)
- [Open Claude Desktop with a link](https://support.claude.com/en/articles/14729294-open-claude-desktop-with-a-link)
