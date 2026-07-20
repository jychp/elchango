# Codex Desktop provider findings

| Feature | Status | Note |
| --- | --- | --- |
| Sessions inventory | ✅ supported | Bounded scan of `~/.codex/sessions` rollouts, filtered to Desktop/user threads. |
| Session live status | ✅ supported | Codex plugin hooks; idle when no hook signal. |
| Session focus | ✅ supported | Exact id-addressed `codex://threads/<id>` deep link; verifies foreground. |
| Session creation | ✅ supported | Neutral `codex://threads/new` deep link. |
| Commands | ⚠️ partial | Types into the frontmost window with no input-target marker (Electron exposes none); acts on whatever thread is on screen and the user confirms the result. |

Status legend: `✅ supported`, `⚠️ partial` (implemented with a behavioral limitation), `❌ not supported`.

## Scope and status

This provider targets the Codex desktop app shipped inside ChatGPT on macOS
(bundle `com.openai.codex`, URL scheme `codex://`). It reads the persistent
rollout files the desktop app writes under `~/.codex/sessions`. The Codex CLI
(`codex-tui`, `codex_exec`, `codex_cli_rs`) and subagent threads are outside its
scope: only rollouts whose `originator` is `Codex Desktop` and whose
`thread_source` is `user` are included.

The native Swift provider implements a bounded, read-only inventory with stable
native identity, workspace and repository mapping, hook-driven live state (idle
when no hook signal), exact session focus via an id-addressed deep link
(`codex://threads/<thread-id>`), and a neutral new session via the new-thread
deep link (`codex://threads/new`). Command dispatch is wired for all four
semantic commands (`accept` is a double Command+Return; the text commands type a
prompt into the composer and submit). Because Electron/Chromium exposes no
input-target marker, dispatch types into the frontmost window and the user
confirms the result. Malformed Codex records degrade only this provider.

Current behavior:

- Inventory: bounded, read-only, filtered to Desktop/user threads.
- State: driven by Codex plugin hooks; a session with no hook signal is idle at
  persisted confidence.
- Selected session: no authoritative static signal exists, so the provider
  reports the session elChango last focused as selected (kept until it leaves the
  inventory). This drives only the deck's selected highlight; commands do not
  depend on it (see below).
- Existing-session focus: Codex Desktop registers an exact, id-addressed deep
  link (`codex://threads/<thread-id>`). The thread id equals the rollout session
  id used as the native id, so focus targets the exact session and verifies that
  the app came to the foreground. Selection cannot be read back statically, so
  the exactness comes from the id in the link, not from a post-action
  selected-thread check.
- New session: Codex Desktop's deep-link router maps `codex://threads/new` to a
  neutral new-thread surface; with no `prompt` query parameter it opens the
  composer and submits nothing. The action opens that link and verifies the app
  came to the foreground.
- Commands: a command acts on whatever thread Codex has on screen; the only
  requirement is that Codex is the frontmost app (no session targeting, no
  re-focus). Recipes: `accept` is a double Command+Return; `create_pr`,
  `commit_push`, and `compact` type a prompt into the focused composer and submit
  with Command+Return. Electron/Chromium exposes no input-target marker, so the
  user is responsible for having the right thread in front and confirms the
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

The observations recorded here provide the primary evidence and share versioned
fixtures with the Swift tests under `contracts/providers/codex/v1/`.

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

Undocumented Codex Desktop internals (renderer selection state, deep-link
router behavior) are listed under open questions.

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

Selected session: no static signal. `~/.codex/.codex-global-state.json` persists
`selected-project` (`{type, projectId}`) but no selected or active *thread* id.
The active thread likely lives in renderer state (the app's Chromium
`Local Storage`/`Session Storage` leveldb), which was not parsed. For the deck's
selected highlight only, the provider tracks the session elChango last focused
(set on a verified focus) and reports it as `selectedNativeSessionID`, dropped
when it leaves the inventory; if the user switches threads inside Codex it goes
stale until elChango focuses again. Commands do not use this signal: a command
acts on whatever Codex has on screen and only requires Codex to be frontmost.
Reading the true active thread from renderer state remains an open question.

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

The handled states are:

| State | Deck color | Produced by |
| --- | --- | --- |
| `working` | blue | hooks `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `PreCompact`, `PostCompact`, `SubagentStart`, `SubagentStop`; a `Stop` while subagents remain active |
| `waiting` | orange | hook `PermissionRequest` (with the tool name) |
| `done` | green | hook `Stop` when no subagents remain active |
| `error` | red | None established (Codex hooks expose no terminal error event) |
| `idle` | gray | hook `SessionStart`; no live hook signal (persisted confidence) |
| `unknown` | gray | a `working`/`waiting` hook older than ten minutes without a terminal event |

State-model notes:

- **Which state means "green".** Only the hook `Stop` (with no active subagents)
  produces `done`. No persisted-file path can reach `done` independently.
- **Terminal signal handling.** `Stop` is the single terminal signal; it maps to
  `done`. A `Stop` received while subagents are active stays `working` until they
  finish. Codex exposes no terminal status field, so there is no `done`/`error`
  split at the terminal event.
- **Stale-signal expiry.** A non-terminal (`working`/`waiting`) hook older than
  ten minutes without a terminal event degrades to `unknown` (gray) with explicit
  degraded detail.
- **Error surfacing.** `None established`. Codex hooks provide no terminal error
  event, so no `error` (red) tile is produced; a `turn_aborted` rollout marker is
  used only for ordering, never for state.
- **Retained metadata.** The store retains only session id, active subagent
  count, and the latest event, state, timestamp, confidence, and bounded detail.
  It retains no prompt, assistant, tool input/output, or transcript content.

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
transcript content. Hook signals are correlated to a session by matching the
hook `session_id` to the rollout `session_id`/`id`.

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
`projectless-thread-ids`), but a positional shortcut is unnecessary
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

All four commands are wired. A command acts on whatever thread Codex has on
screen. `executeCommand` verifies only that Codex is the frontmost application
(no session targeting, no re-focus), then dispatches a per-command recipe via the
shared automation boundary:

- `accept`: a double Command+Return (`postShortcut` twice). No text.
- `create_pr`, `commit_push`, `compact`: type a fixed prompt into the focused
  composer (`create_pr` / `commit_push` use the same instructions as the other
  providers; `compact` types `/compact`), then submit with a single
  Command+Return.

This is why commands work whether the thread was focused from the deck or by
hand: the requirement is just "Codex is in front". All return
`COMMAND_DISPATCHED`; the user verifies the effect. Two honest limits: there is
no post-action confirmation the keystrokes landed on the intended thread (elChango
cannot read the active thread), and the text commands type into whatever the
composer focus is, because the Electron/Chromium app exposes no verified
input-target marker (`dispatchFrontmostText` skips the accessibility-marker
verification that `dispatchText` uses for Claude Code). Refining a recipe means
editing
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
  it submits nothing. Command dispatch requires only that Codex is frontmost and
  runs a fixed per-command recipe on the active window: `accept` sends only
  keystrokes, and the text commands type a fixed instruction (never free-form
  user text) and submit. Surfaces carry only stable semantic ids, so no arbitrary
  prompt text can be injected through a command.
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

- The active *thread* is not persisted in `.codex-global-state.json` (only the
  selected project is); it lives in the app's Chromium leveldb, which the
  provider does not parse. The provider therefore cannot read the active thread:
  focus relies on the exact id in the deep link and a frontmost check, and a
  command cannot be confirmed to have landed on a specific thread.
- New session opens the composer, but the app persists no selected-thread
  signal, so the resulting thread cannot be read back; success is confirmed by
  the app coming to the foreground.
- The Electron/Chromium desktop app exposes no agent prompt-input accessibility
  marker, so command dispatch types into the frontmost window without an
  input-target check (`dispatchFrontmostText`), by design.
- Codex Desktop stores the rollout native id in an undocumented format; its
  stability across resume is not guaranteed by the harness and must be
  revalidated when Codex changes.
- The Codex Desktop hook event vocabulary beyond the documented set, and whether
  an `http` hook type exists, are undocumented and may change.

## References

- [Codex hooks reference](https://learn.chatgpt.com/docs/hooks)
- [Codex advanced configuration](https://developers.openai.com/codex/config-advanced)
- [Codex documentation](https://developers.openai.com/codex/)
