# Codex Desktop provider findings

## Scope and status

This provider targets the Codex desktop app shipped inside ChatGPT on macOS
(bundle `com.openai.codex`, URL scheme `codex://`). It reads the persistent
rollout files the desktop app writes under `~/.codex/sessions`. The Codex CLI
(`codex-tui`, `codex_exec`, `codex_cli_rs`) and subagent threads are outside its
scope: only rollouts whose `originator` is `Codex Desktop` and whose
`thread_source` is `user` are included.

The native Swift provider implements a bounded, read-only inventory with stable
native identity, workspace and repository mapping, hook-driven live state (idle
by default), exact session focus via an id-addressed deep link
(`codex://threads/<thread-id>`), and a neutral new session via the new-thread
deep link (`codex://threads/new`). A first, naive command dispatch is wired for
all four semantic commands (`accept` is a double Command+Return; the text
commands type a prompt into the composer and submit); it is best-effort and the
user verifies the result. Malformed Codex records degrade only this provider.

Current conservative verdicts:

- Inventory: `INVENTORY_SUPPORTED` for the observed installation.
- State: derived only from Codex plugin hooks at observed confidence; with no
  live hook a session is idle at persisted confidence. Live hook observation
  remains outstanding: `UNPROVEN_REQUIRES_LIVE_HOOK_OBSERVATION`.
- Selected session: `TRACKED_FROM_FOCUS`. No authoritative static signal exists,
  so the provider reports the session elChango last focused as selected (dropped
  when it leaves the inventory). Command targeting is additionally gated on Codex
  being frontmost. Without this, commands could never be enabled because the deck
  resolves a command target from the selected session.
- Existing-session focus: `FOCUS_DISPATCH_VERIFIED`. Codex Desktop registers an
  exact, id-addressed deep link (`codex://threads/<thread-id>`, observed in the
  app bundle). The thread id equals the rollout session id used as the native
  id, so focus targets the exact session and verifies that the app came to the
  foreground. Selection cannot be read back statically, so the exactness comes
  from the id in the link, not from a post-action selected-thread check.
- New session: `NEW_SESSION_REQUESTED`. Codex Desktop's deep-link router maps
  `codex://threads/new` to a neutral new-thread surface; with no `prompt` query
  parameter it opens the composer and submits nothing. The action opens that
  link and verifies the app came to the foreground.
- Commands: `COMMAND_DISPATCHED` (naive, all four). The provider first focuses
  the exact thread via the deep link, then dispatches a per-command recipe:
  `accept` is a double Command+Return; `create_pr`, `commit_push`, and `compact`
  type a prompt into the composer and submit with Command+Return. Codex exposes
  no static selected-thread signal and the text commands have no verified
  input-target marker, so dispatch is best-effort and the user verifies the
  result.

The provider descriptor declares `focus_session` and `execute_command` (per
session) and `new_session` (provider level); every Desktop thread is addressable
by id through the deep link, so focus and all four commands are offered for all
sessions. Recipes live in `CodexProvider.commands` and `dispatchRecipe(for:)`;
refining a recipe (or proving an exact input-target) is a change there.

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
- The app bundle (`/Applications/ChatGPT.app/Contents/Resources/app.asar`)
  constructs `codex://threads/<thread-id>` deep links (the "Open in app"
  action), giving an exact, id-addressed focus route.

Conclusions are limited to what these observations support; hypotheses about
launch, selection, and live hook delivery are listed under open questions.

## Selected session

No static signal. `~/.codex/.codex-global-state.json` persists `selected-project`
(`{type, projectId}`) but no selected or active *thread* id. The active thread
likely lives in renderer state (the app's Chromium `Local Storage`/`Session
Storage` leveldb), which was not parsed.

To make commands usable, the provider tracks the session elChango last focused
(set on a verified focus or command dispatch) and reports it as
`selectedNativeSessionID`, marking that session selected. The reference is
dropped as soon as the session leaves the inventory. This is a proxy, not ground
truth: if the user switches threads inside Codex, the tracked selection goes
stale until elChango focuses again. Command targeting is therefore additionally
gated on Codex being frontmost, and `executeCommand` re-focuses the exact thread
by deep link before dispatching. Reading the true active thread from renderer
state remains an open question.

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

Live state comes only from hooks. A session with no live hook signal is idle
(gray) at persisted confidence, matching the Claude Code provider: green (done),
blue (working), and orange (waiting) are never derived from persisted files, so
the deck never shows a stale done/working state by default.

The provider still scans a bounded rollout tail, but only to timestamp the
newest `event_msg` lifecycle marker (`task_started`, `task_complete`,
`turn_aborted`) for last-activity ordering; the marker never sets a session
state.

Live hooks: Codex supports a plugin hook system with the same file schema and
payload field names as Claude Code, but only `type: "command"` handlers run, so
elChango relays through `elChangoHookReporter --provider codex` (as the Cursor
plugin does), which POSTs a sanitized payload to
`http://127.0.0.1:8765/api/hooks/codex`. The ten events are exactly those in the
official docs (https://learn.chatgpt.com/docs/hooks); Codex has no `SessionEnd`
event. `CodexActivityStore` maps them at observed confidence:

- `SessionStart`: gray, idle;
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

`marketplace add` reads the marketplace manifest from the repo's
`.agents/plugins/marketplace.json` (Codex's primary location; it also accepts the
legacy `.claude-plugin/marketplace.json`, but never `.codex-plugin/marketplace.json`).
elChango therefore ships the Codex marketplace at `.agents/plugins/marketplace.json`
with `source: ./plugins/codex`, kept separate from the Claude
(`.claude-plugin/marketplace.json`) and Cursor (`.cursor-plugin/marketplace.json`)
marketplaces.

Trusting the hooks (required). Codex does not run a plugin's command hooks until
they are trusted: "Non-managed command hooks must be reviewed and trusted before
they run." Installing the plugin is not enough. The first time a Codex session
loads the plugin, Codex prompts to review and trust its command hooks; approve
them so `elChangoHookReporter` may run. Trust is persisted per hook in
`~/.codex/config.toml` under
`[hooks.state."elchango@<marketplace>:hooks/hooks.json:<event>:0:0"]` as a
`trusted_hash` (a sha256 of the exact command); changing the hook command
re-prompts. There is no dedicated `codex plugin trust` CLI command. For
non-interactive or CI runs only, `codex --dangerously-bypass-hook-trust` runs
enabled hooks without the trust gate for that invocation; it is dangerous and is
not recommended for normal use. Until the hooks are trusted, the deck still shows
Codex sessions as idle at persisted confidence (no live state).

A working or waiting hook older than ten minutes without a terminal event
becomes unknown with explicit degraded detail. The store retains only session
id, active subagent count, and the latest event, state, timestamp, confidence,
and bounded detail. It retains no prompt, assistant, tool input/output, or
transcript content. Live evidence must still confirm that a hook `session_id`
equals the rollout `session_id`/`id` and that the expected event sequences
reliably represent turns and waiting states.

## Focus and launch

Focus uses an exact, id-addressed deep link. The Codex Desktop app bundle
registers the `codex://` scheme and constructs `codex://threads/<thread-id>`
links (the "Open in app" action; observed in
`/Applications/ChatGPT.app/Contents/Resources/app.asar`). The thread id equals
the rollout `session_id`/`id` used as the native id, so `focus`:

1. verifies the target session exists in the current inventory;
2. opens `codex://threads/<native-id>` (which navigates Codex Desktop to that
   exact thread and foregrounds the app);
3. polls briefly for the app to become frontmost.

It returns `FOCUS_DISPATCH_VERIFIED` when the app foregrounds, otherwise
`FOCUS_DISPATCH_UNVERIFIED` (the link was still opened). Codex Desktop persists
no selected or active *thread* id (`selected-project` is persisted, but not a
thread), so selection cannot be read back after acting; the exactness comes from
the id carried in the deep link rather than a post-action selected-thread check.

This deep link supersedes the earlier plan of a Claude-style focus-by-position
shortcut. The sidebar order is still persisted and reconstructable from
`~/.codex/.codex-global-state.json` (`pinned-thread-ids`, `local-projects` +
`thread-project-assignments`, `sidebar-project-thread-orders`,
`projectless-thread-ids`; see POC 07), but a positional shortcut is unnecessary
now that each thread is directly addressable by id, and no sidebar-position
keyboard shortcut is known for Codex Desktop.

New session uses the same deep-link router. `codex://threads/new` resolves to a
neutral new-thread surface (`kind: newThread`); the router only attaches input
when a `prompt`, `originUrl`, or `path` query parameter is present, so the bare
link opens the composer and submits nothing. `openNew` opens it and verifies the
app foregrounds, returning `NEW_SESSION_REQUESTED` (or
`NEW_SESSION_DISPATCH_UNVERIFIED` if the app never comes forward; the link is
still opened). Both routes were read from the app bundle's deep-link parser
(`case 'threads': if segment[0] === 'new' -> newThread, else -> localConversation`).

## Semantic commands

Naive first pass: all four commands are wired, best-effort.

Because Codex Desktop exposes no static selected-thread signal, `executeCommand`
first focuses the exact thread through its deep link (`codex://threads/<id>`,
which foregrounds the app and selects the thread by id) and confirms the app is
frontmost. It then dispatches a per-command recipe via the shared automation
boundary:

- `accept`: a double Command+Return (`postShortcut` twice). No text.
- `create_pr`, `commit_push`, `compact`: type a fixed prompt into the focused
  composer (`create_pr` / `commit_push` use the same instructions as the other
  providers; `compact` types `/compact`), then submit with a single
  Command+Return.

All return `COMMAND_DISPATCHED`; the user verifies the effect. Two honest limits:
there is no post-action confirmation the keystrokes landed on the intended thread
(the target is only as exact as the id in the focus link plus the frontmost
check), and the text commands type into whatever the composer focus is, because
the Electron/Chromium app exposes no verified input-target marker
(`dispatchFrontmostText` skips the accessibility-marker verification that
`dispatchText` uses for Claude Code). Refining a recipe means editing
`CodexProvider.commands` / `dispatchRecipe(for:)`. Surfaces still emit only
stable semantic ids, never arbitrary prompt text.

## Safety and target verification

- Persistent metadata is parsed with bounded first-line and tail reads;
  conversation payloads are never consumed.
- Inventory identities are correlated only by exact native id.
- Provider actions share the serialized native automation boundary and the
  process-wide privileged action gate with Cursor and Claude.
- Focus acts only on a target present in the current inventory and only through
  the exact id-addressed deep link; it never submits prompt text. New session
  opens only the neutral `codex://threads/new` link with no query parameters, so
  it submits nothing. Command dispatch first focuses the exact thread, then runs
  a fixed per-command recipe: `accept` sends only keystrokes, and the text
  commands type a fixed instruction (never free-form user text) and submit.
  Surfaces carry only stable semantic ids, so no arbitrary prompt text can be
  injected through a command.
- Hook payloads are sanitized to a minimal metadata allow-list
  (`hook_event_name`, `session_id`, `cwd`, `transcript_path`, `tool_name`,
  `permission_mode`, `turn_id`) before reaching the provider.
- Surfaces carry semantic ids only, never arbitrary recipe text.

## Degradation behavior

Malformed records, absent required metadata, and unreadable rollouts reject or
skip only the affected record and never disable Cursor or Claude or prevent the
host from starting. A missing `~/.codex/sessions` directory fails the Codex
snapshot alone. Sessions without a fresh hook are reported as idle at persisted
confidence; a stale working or waiting hook older than ten minutes becomes
unknown with explicit degraded detail.

## Limitations and open questions

- Live Codex Desktop hooks have not been observed reaching elChango end to end.
  Plugin install uses the official `codex plugin` commands (see the state
  section); a live install has not yet been run against a machine from the app.
- Correlation of hook `session_id` with rollout `session_id`/`id` needs live
  proof.
- The active *thread* is not persisted in `.codex-global-state.json` (only the
  selected project is); it likely lives in the app's Chromium leveldb, which was
  not parsed. Selection stays unknown, so focus cannot be confirmed by reading
  back a selected thread; it relies on the exact id in the deep link and a
  frontmost check.
- Focus uses the id-addressed deep link (`codex://threads/<id>`), so the
  reconstructable sidebar order (POC 07) and the unknown sidebar-position
  keyboard shortcut are no longer needed for focus.
- New session opens the composer but the app persists no selected-thread signal,
  so the resulting thread cannot be read back; success is confirmed only by the
  app coming to the foreground.
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
- `scripts/poc/codex/04_codex_desktop_focus.py`: exact per-thread deep link
  (`codex://threads/<id>`). Verdict: `FOCUS_DEEP_LINK_AVAILABLE`.
- `scripts/poc/codex/05_codex_new_session.py`: neutral new-thread deep link
  (`codex://threads/new`). Verdict: `NEW_SESSION_DEEP_LINK_AVAILABLE`.
- `scripts/poc/codex/06_codex_command_dispatch.py`: command preflight; documents
  the naive recipes for all four commands. Verdict: `COMMANDS_WIRED_NAIVE`.
- `scripts/poc/codex/07_codex_sidebar_order.py`: sidebar-order reconstruction
  from `~/.codex/.codex-global-state.json` (pinned, projects, manual orders,
  projectless) cross-referenced with rollout last activity. Verdict:
  `SIDEBAR_ORDER_RECONSTRUCTED`.

## References

- [Codex hooks reference](https://learn.chatgpt.com/docs/hooks)
- [Codex advanced configuration](https://developers.openai.com/codex/config-advanced)
- [Codex documentation](https://developers.openai.com/codex/)
