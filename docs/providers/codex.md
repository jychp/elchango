# Codex Desktop provider findings

## Scope and status

This provider targets the Codex desktop app shipped inside ChatGPT on macOS
(bundle `com.openai.codex`, URL scheme `codex://`). It reads the persistent
rollout files the desktop app writes under `~/.codex/sessions`. The Codex CLI
(`codex-tui`, `codex_exec`, `codex_cli_rs`) and subagent threads are outside its
scope: only rollouts whose `originator` is `Codex Desktop` and whose
`thread_source` is `user` are included.

The native Swift provider implements a bounded, read-only inventory with stable
native identity, workspace and repository mapping, a conservative
rollout-derived state, and a plugin hook path for live lifecycle state. Focus,
new-session, and command dispatch are implemented but fail closed. Malformed
Codex records degrade only this provider.

Current conservative verdicts:

- Inventory: `INVENTORY_SUPPORTED` for the observed installation.
- State: implemented from rollout tails at persisted confidence and from Codex
  plugin hooks at observed confidence, but live hook observation remains
  outstanding: `UNPROVEN_REQUIRES_LIVE_HOOK_OBSERVATION`.
- Selected session: `UNPROVEN_NO_STATIC_SELECTED_SESSION_SIGNAL`. No
  authoritative static signal was found, so selection is always reported as
  unknown.
- Existing-session focus: `FOCUS_NOT_VERIFIED_REQUIRES_LIVE`. No verifiable
  exact-session mechanism exists; the action fails closed.
- New session: `NEW_SESSION_UNVERIFIED_REQUIRES_LIVE`. The `codex://` scheme is
  registered but no neutral new-session route is confirmed; the action fails
  closed.
- Commands: `UNPROVEN_REQUIRES_LIVE_TARGET_EVIDENCE`. No selected-session
  signal and no verified prompt-input target exist; commands fail closed.

Because no privileged capability is proven without a live experiment, the
provider descriptor declares an empty capability set. Sessions render with live
state, but no focus, new-session, or command buttons are offered. Enabling a
capability after the corresponding live experiment is a one-line change to the
descriptor and `sessionCapabilities`.

## Tested versions and environment

- Observation date: July 19, 2026.
- Operating system: macOS (Darwin 25.5.0).
- Codex Desktop app: ChatGPT.app `CFBundleShortVersionString` 26.715.31925,
  bundle identifier `com.openai.codex`, Chromium runtime 150.
- Rollout `cli_version` values observed for Desktop sessions ranged from
  `0.139.0` to `0.145.0-alpha.18`.
- Inventory root: `~/.codex/sessions`.
- Title index: `~/.codex/session_index.jsonl`.

## Evidence

The executable POCs under `scripts/poc/codex/` provide the primary evidence and
share versioned fixtures with the Swift tests under
`contracts/providers/codex/v1/`.

Observations from the tested installation:

- 301 rollouts across `~/.codex/sessions` and `~/.codex/archived_sessions`
  carried `"originator":"Codex Desktop"`. Of the Desktop metas, 139 were
  `thread_source: user`, 148 were `subagent`, and 14 omitted `thread_source`.
- The live `~/.codex/sessions` tree held a small number of current Desktop user
  sessions; the CLI accounts for the large majority of rollouts.
- `~/.codex/config.toml` recorded `[hooks.state."...hooks/hooks.json:session_start:0:0"]`
  entries, confirming the desktop app honors the plugin hook mechanism.

Conclusions are limited to what these observations support; hypotheses about
focus, launch, selection, and live hook delivery are listed under open
questions.

## Selected session

Unresolved. `~/.codex/.codex-global-state.json` persists `selected-project`
(`{type, projectId}`) but no selected or active *thread* id. The active session
likely lives in renderer state (the app's Chromium `Local Storage`/`Session
Storage` leveldb), which was not parsed. The provider reports
`selectedNativeSessionID = nil` and marks every session unselected until a live
signal is established. Commands remain disabled without it.

## Inventory and identity

Each session run is a JSON-lines rollout at
`~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`. The first line is a
`session_meta` record whose `payload` carries identity and workspace fields.
Inventory reads only that bounded first line plus a bounded tail; it never reads
conversation content.

Observed `session_meta.payload` shape (schema varies by version):

```json
{
  "id": "019ed197-4c4c-7c01-81cf-0ca716ae3de7",
  "session_id": "019f4948-1f98-7db2-8f6a-b14128f3a556",
  "cwd": "/path/to/worktree",
  "originator": "Codex Desktop",
  "thread_source": "user",
  "cli_version": "0.145.0-alpha.18",
  "git": { "repository_url": "git@github.com:example/repo.git",
           "commit_hash": "..." }
}
```

Identity rules:

- The native session id is `session_id` when present, otherwise `id`. Desktop
  user rollouts frequently carry only `id`; subagent rollouts carry both.
- A thread can span several rollout files after resume. Inventory deduplicates
  by native id, keeping the rollout with the newest last activity.
- A confirmed Desktop user record missing a non-empty `id`/`session_id` or a
  non-empty `cwd` fails that record conservatively.
- Empty or unparseable rollout artifacts (for example after a crash) cannot be
  classified by `originator` and are skipped, not treated as errors.

Metadata is cached by file modification time and size; an appended (growing)
rollout invalidates its cache entry so live state is re-read.

## Workspace mapping

Every observed Desktop user record supplied `cwd` (the current working
directory or worktree; the desktop app commonly uses `~/.codex/worktrees/...`).
Most also supplied `git.repository_url`. The provider maps:

- `workspacePath` = `cwd`;
- `workspaceID` = `git.repository_url` when present, otherwise `cwd`.

The provider uses these direct fields rather than transcript content. Missing
`cwd` fails the record; a missing `git` block falls back to `cwd` for the
workspace identity.

## State model and hooks

State has two sources, preferring live hooks over the persisted fallback.

Persisted fallback (no live hook): the provider scans a bounded rollout tail for
the newest `event_msg` lifecycle marker and maps it at persisted confidence:

- `task_started` (no later terminal marker): blue, working;
- `task_complete`: green, done;
- `turn_aborted`: gray, idle;
- no marker: gray, idle.

Observation, not conclusion: no persisted "waiting for approval" record was
found in Desktop rollouts, so the waiting (orange) state is not derivable from
rollouts.

Live hooks: Codex supports a plugin hook system with the same file schema and
payload field names as Claude Code, but only `type: "command"` handlers run, so
elChango relays through `elChangoHookReporter --provider codex` (as the Cursor
plugin does), which POSTs a sanitized payload to
`http://127.0.0.1:8765/api/hooks/codex`. `CodexActivityStore` maps events at
observed confidence:

- `SessionStart`, `SessionEnd`: gray, idle;
- `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `PreCompact`, `PostCompact`,
  `SubagentStart`, `SubagentStop`: blue, working;
- `PermissionRequest`: orange, waiting (with the tool name);
- `Stop`: green, done, unless subagents are still active, in which case it stays
  blue until they finish.

Plugin install status is detected read-only by `CodexPluginInspector`, which
classifies the elChango plugin under `~/.codex/plugins/cache/<marketplace>/elchango/<ref>/.codex-plugin/plugin.json`
(missing, matching, mismatched, managed, malformed, or unreadable) and surfaces
it in the app's Diagnostics. Install and update use Codex's official plugin
commands via `CodexPluginInstaller`: `codex plugin marketplace add jychp/elchango`,
then `codex plugin marketplace upgrade elchango`, then `codex plugin add
elchango@elchango`. The menu offers Install/Update Codex Plugin when the plugin
is missing or mismatched, mirroring the Claude Code flow. The `codex` executable
is located across well-known paths plus `PATH`; identifiers are passed as fixed
argument arrays with no shell involved.

A working or waiting hook older than ten minutes without a terminal event
becomes unknown with explicit degraded detail. The store retains only session
id, active subagent count, and the latest event, state, timestamp, confidence,
and bounded detail. It retains no prompt, assistant, tool input/output, or
transcript content. Live evidence must still confirm that a hook `session_id`
equals the rollout `session_id`/`id` and that the expected event sequences
reliably represent turns and waiting states.

## Focus and launch

None verified.

The sidebar order is persisted and reconstructable (see POC 07). Codex Desktop
stores it in `~/.codex/.codex-global-state.json`:

- `pinned-thread-ids`: pinned threads, shown first;
- `local-projects` (`{projectId: {id, name, rootPaths, createdAt, updatedAt}}`)
  with `thread-project-assignments` (`{threadId: {projectKind, projectId, cwd}}`)
  group threads into projects, each project ordered by its threads' last
  activity;
- `sidebar-project-thread-orders` (`{projectId: {threadIds: [...]}}`) is a manual
  order override within a project; without it, threads sort by last activity;
- `projectless-thread-ids`: loose threads, by last activity.

The global-state thread ids correlate with the rollout `session_id`/`id`, so the
order can be mapped onto the inventory. This provides the *order* half of a
Claude-style focus-by-position mechanism. Two pieces are still missing and
require live evidence:

- no sidebar-position keyboard shortcut is known for Codex Desktop (Claude uses
  `Cmd+1`..`Cmd+9`);
- `selected-project` is persisted but no selected or active *thread* id is, so a
  focus cannot be verified after acting.

`focus` therefore verifies the target exists and then fails closed with
`FOCUS_NOT_VERIFIED_REQUIRES_LIVE` rather than foregrounding the app on an
unverifiable target. The `codex://` scheme is registered, but no neutral
new-session route was confirmed and no no-submit guarantee was established, so
`openNew` fails closed with `NEW_SESSION_UNVERIFIED_REQUIRES_LIVE`.

## Semantic commands

None established.

Command dispatch requires an exact selected-session signal and a verified agent
prompt-input target. Codex Desktop exposes neither statically, and no official
command or shortcut mapping is proven for any semantic id. `executeCommand`
therefore fails closed with `TARGET_NOT_SELECTED`. The stable semantic ids
remain `accept`, `create_pr`, `commit_push`, and `compact`; no provider recipe
is claimed.

## Safety and target verification

- Persistent metadata is parsed with bounded first-line and tail reads;
  conversation payloads are never consumed.
- Inventory identities are correlated only by exact native id.
- Provider actions share the serialized native automation boundary and the
  process-wide privileged action gate with Cursor and Claude.
- Focus, new-session, and command actions fail closed: they never act on an
  unverified target and never submit prompt text.
- Hook payloads are sanitized to a minimal metadata allow-list
  (`hook_event_name`, `session_id`, `cwd`, `transcript_path`, `tool_name`,
  `permission_mode`, `turn_id`) before reaching the provider.
- Surfaces carry semantic ids only, never arbitrary recipe text.

## Degradation behavior

Malformed records, absent required metadata, and unreadable rollouts reject or
skip only the affected record and never disable Cursor or Claude or prevent the
host from starting. A missing `~/.codex/sessions` directory fails the Codex
snapshot alone. Sessions without a fresh hook are reported at persisted
confidence from the rollout tail; a stale working or waiting hook older than ten
minutes becomes unknown with explicit degraded detail.

## Limitations and open questions

- Live Codex Desktop hooks have not been observed reaching elChango end to end.
  Plugin install uses the official `codex plugin` commands (see the state
  section); a live install has not yet been run against a machine from the app.
- Correlation of hook `session_id` with rollout `session_id`/`id` needs live
  proof.
- The active *thread* is not persisted in `.codex-global-state.json` (only the
  selected project is); it likely lives in the app's Chromium leveldb, which was
  not parsed. Selection stays unknown until that signal is read or a live hook
  supplies it.
- The sidebar order is reconstructable (POC 07), but no sidebar-position
  keyboard shortcut is known for Codex Desktop, so a focus-by-position mechanism
  cannot be completed or verified without live evidence.
- No confirmed neutral new-session route was found.
- No agent prompt-input accessibility target has been identified for the
  Electron/Chromium desktop app; command dispatch remains unproven.
- Native id stability across Codex Desktop resume is unproven.
- Exact Codex Desktop hook event vocabulary beyond the documented set, and
  whether an `http` hook type will ever exist, are undocumented and may change.

## POCs

- `scripts/poc/codex/01_codex_desktop_session_inventory.py`: bounded persistent
  inventory, Desktop/user filtering, native identity, workspace and repository
  mapping, titles, ordering. Verdict: `INVENTORY_SUPPORTED`.
- `scripts/poc/codex/02_codex_desktop_state_from_rollout.py`: rollout-tail
  lifecycle-marker state derivation. Verdict: `STATE_DERIVED_PERSISTED`
  (waiting not derivable from rollouts).
- `scripts/poc/codex/03_codex_hook_probe.py`: generated Codex plugin hooks,
  event-to-state mapping, payload sanitization, self-check. Verdict:
  `UNPROVEN_REQUIRES_LIVE_HOOK_OBSERVATION`.
- `scripts/poc/codex/04_codex_desktop_focus.py`: focus verification gap.
  Verdict: `FOCUS_NOT_VERIFIED_REQUIRES_LIVE`.
- `scripts/poc/codex/05_codex_new_session.py`: neutral new-session candidates.
  Verdict: `NEW_SESSION_CANDIDATE_UNVERIFIED`.
- `scripts/poc/codex/06_codex_command_dispatch.py`: command preflight and
  refusal. Verdict: `UNPROVEN_REQUIRES_LIVE_TARGET_EVIDENCE`.
- `scripts/poc/codex/07_codex_sidebar_order.py`: sidebar-order reconstruction
  from `~/.codex/.codex-global-state.json` (pinned, projects, manual orders,
  projectless) cross-referenced with rollout last activity. Verdict:
  `SIDEBAR_ORDER_RECONSTRUCTED`.

## References

- [Codex hooks reference](https://learn.chatgpt.com/docs/hooks)
- [Codex advanced configuration](https://developers.openai.com/codex/config-advanced)
- [Codex documentation](https://developers.openai.com/codex/)
