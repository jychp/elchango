# Claude Code provider findings

This document records provider-specific findings. The executable evidence is in
the numbered POCs under `scripts/poc/`.

## Scope

The target is Claude Code sessions opened by Claude Desktop on macOS. Claude
Cowork and ordinary Claude chats are outside this provider's scope.

## V3.2 reconnaissance

### Observations

On July 17, 2026, `09_claude_code_session_inventory.py` observed:

- 711 persistent Desktop Code records under
  `~/Library/Application Support/Claude/claude-code-sessions`;
- 17 non-archived sessions, matching the session list visible in Claude Desktop;
- five of those 17 sessions with a currently live Claude Code process;
- a unique Desktop `sessionId` and unique Claude Code `cliSessionId` in every
  non-archived record;
- an exact `cwd`, origin workspace, creation time, last activity time, archive
  state, and optional title in every non-archived record;
- one top-level transcript at
  `~/.claude/projects/<encoded-cwd>/<cliSessionId>.jsonl` for every non-archived
  record.

The observed persistent Desktop schema included:

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

The Desktop records can contain large conversation payloads. The POC used a
bounded top-level metadata parser and stopped before those payloads. It matched
transcripts by exact CLI session ID. The separate `~/.claude/sessions` process
registry was used only to annotate process liveness, never to filter inventory.

The official Claude Code hooks documentation defines these relevant events:

- `SessionStart`: a session starts or resumes;
- `UserPromptSubmit`: a prompt is submitted before Claude processes it;
- `Notification`: includes `permission_prompt`, `idle_prompt`,
  `agent_needs_input`, and `agent_completed` notification types;
- `Stop`: Claude finishes responding;
- `StopFailure`: the turn ends because of an API error;
- `SessionEnd`: the session terminates.

Official hook payloads share `session_id`, `transcript_path`, `cwd`, and
`hook_event_name`. Claude Code supports both command and native HTTP handlers.
The transcript is written asynchronously and can lag the current hook event.

`10_claude_code_hook_probe.py` generates a non-installed hook configuration,
sanitizes hook payloads, and analyzes captured evidence. Its synthetic
self-check passed, but no live hook events were recorded because real-session
testing is deferred while ongoing sessions must not be disturbed.

### Conclusions

- Read-only inventory of persistent, non-archived Claude Desktop Code sessions
  is a supported adapter candidate on the observed installation.
- The Desktop `sessionId` is the persistent UI identity. `cliSessionId` is the
  Claude Code runtime, transcript, and expected hook identity.
- `cwd`, `originCwd`, and `lastActivityAt` directly provide workspace mapping
  and provider-neutral ordering.
- The process registry is useful only as a liveness annotation. Using it as the
  inventory source would omit 12 of the 17 visible sessions in this snapshot.
- Live hook evidence must confirm that hook `session_id` equals Desktop
  `cliSessionId`.
- State inference must use hooks as its lifecycle source. Transcript content
  must not be used to invent current state.
- Native HTTP hooks are the preferred production candidate because they avoid
  dependence on Claude's shell hook executor.
- Existing-session focus is not supported until an exact target can be verified
  after the action.

### Hypotheses requiring focused experiments

- Persistent Desktop records appear and update promptly as sessions change.
- Desktop `sessionId` and `cliSessionId` remain stable when a session resumes.
- `UserPromptSubmit` followed by `Stop` provides reliable blue-to-green turn
  transitions.
- `Notification` with `permission_prompt` or `agent_needs_input` provides a
  reliable orange transition and a later event clears it.
- Native HTTP hooks can reach the loopback elChango server from Claude Desktop.
- Claude Desktop exposes a verifiable existing-session focus mechanism.

## Conservative V3.2 verdict

- Inventory: `INVENTORY_SUPPORTED` for the observed Claude Desktop version.
- State: `UNPROVEN_REQUIRES_LIVE_HOOK_OBSERVATION`.
- Existing-session targeting: `UNPROVEN_NO_EXACT_FOCUS_MECHANISM`.
- Official new-session launch: documented separately for V3.4 using
  `claude://code/new`.

## V3.3 provider implementation

The read-only provider uses Desktop `sessionId` as its persistent native
identity and `cliSessionId` to correlate official hook events and transcripts.
It excludes archived records, caches unchanged metadata by file modification
time and size, and sorts sessions by `lastActivityAt`.

Measured on July 17, 2026:

- first snapshot of 711 persistent records: 126.2 ms;
- cached snapshot: 12.4 ms;
- resulting non-archived session inventory: 17 sessions.

Sessions without fresh hook evidence are gray with persisted confidence.
Documented hook transitions map as follows:

- `UserPromptSubmit`: blue, working;
- waiting `Notification` types: orange, waiting;
- `Stop`: green, done;
- `StopFailure`: error, rendered orange by the four-color deck;
- `SessionStart` and `SessionEnd`: gray, idle.

A working or waiting signal older than ten minutes without a terminal event
becomes unknown with explicit degraded detail. This timeout is conservative and
will be revisited with live hook evidence. The provider retains no prompt,
assistant, notification message, or transcript content.

Claude session buttons remain disabled because exact existing-session focus is
not verified. Empty and New buttons continue to target Cursor until V3.4 adds
explicit provider selection.

## References

- [Claude Code hooks reference](https://docs.anthropic.com/en/docs/claude-code/hooks)
- [Claude Code common workflows](https://docs.anthropic.com/en/docs/claude-code/common-workflows)
- [Open Claude Desktop with a link](https://support.claude.com/en/articles/14729294-open-claude-desktop-with-a-link)
