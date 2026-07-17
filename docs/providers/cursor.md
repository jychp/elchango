# Cursor Provider Research

## Status

Cursor on macOS is the first provider under feasibility testing. These findings
describe the Cursor version observed on July 16, 2026. They rely on undocumented
internals and must be revalidated when Cursor changes.

Executable evidence lives in `scripts/poc/`. This document summarizes what the
POCs prove and what remains uncertain. It does not replace them.

## M0.1: session inventory

POC: `scripts/poc/01_cursor_session_inventory.py`

Current verdict: `EXPLOITABLE_WITH_PRECAUTIONS`.

### Observed storage

The global database is:

`~/Library/Application Support/Cursor/User/globalStorage/state.vscdb`

It can be read while Cursor is running by using SQLite `mode=ro` and
`PRAGMA query_only=ON`. SQLite reads current WAL-backed updates without requiring
a copy of the database or stopping Cursor.

Relevant structures observed:

- `composerHeaders` exposes composer IDs, workspace IDs, timestamps, archive
  state, subagent state, and JSON header metadata.
- `ItemTable` exposes additional UI state, including per-agent visibility
  markers.
- `workspaceStorage/*/workspace.json` maps workspace hashes to local paths.
- Composer header JSON can also contain the workspace or worktree path directly.

All names, fields, and keys above are undocumented Cursor implementation details.
The provider must validate the schema and fail clearly when it changes.

### Candidate session heuristic

The POC currently treats a record as a candidate user session when it:

- is not archived;
- is not a draft;
- is not ephemeral;
- is not a subagent;
- has an observed `lastUpdatedAt`.

This heuristic excludes stale empty-window and ephemeral records. It is not yet
a proven definition of an open agent tab.

### Live observation

A 90-second observation with a 0.2-second polling interval covered:

1. switching to another session;
2. returning to the original session;
3. creating a session;
4. interacting with it;
5. closing or archiving it.

The POC observed:

- visibility changes during session switches;
- the new session being added;
- one composer ID retained throughout the observed lifecycle;
- the session being removed from the candidate set after closure or archival;
- changes within the 0.2-second polling resolution.

This validates one live lifecycle. It does not prove stability across Cursor
restarts or upgrades.

### Signal interpretation

Several old sessions can simultaneously retain `visible=true`. Therefore,
`visible=true` is not sufficient to identify the globally active session.

The visibility timestamp can change while the composer header `lastUpdatedAt`
remains unchanged. They are distinct signals:

- visibility metadata reflects UI visibility activity;
- header update time reflects other composer metadata changes.

`isArchived` has not been proven equivalent to whether an agent tab is currently
open or closed. In the observed test, closing or archiving removed the session
from the POC candidate set because that set excludes archived records.

## M0.2: selected session detection

POC: `scripts/poc/02_cursor_active_session.py`

Current verdict: `SUPPORTED` for identifying Cursor's selected agent session.

The `ItemTable` key `cursor/glass.selectedAgent` contains one composer ID. During
a 90-second live test, it matched three user-driven switches among sessions in
different workspaces. Opening a canvas did not change the selected ID.

The selected ID can be validated against `composerHeaders` and mapped to its
workspace. A consumer must return `unknown` if the ID is absent, does not resolve,
or resolves to an archived, draft, ephemeral, subagent, or unmapped record.

Visibility remains auxiliary. One correctly selected session had `visible=false`
during the test, while many historical sessions retained `visible=true`.
Therefore visibility must not override or invalidate a valid `selectedAgent`.

The workspace tab state reports the foreground content separately. This allows
elChango to preserve the selected agent while a canvas, diff, browser, terminal,
or file is displayed. The result means "selected agent", not "control with
keyboard focus."

## M0.3 and M0.4: DB-only execution state

POCs:

- `scripts/poc/03_cursor_session_state_from_db.py`
- `scripts/poc/04_cursor_bubble_state_from_db.py`

Aggregate fields in `composerData:<composer-id>` can remain stale throughout a
turn, so they are insufficient by themselves. Individual
`bubbleId:<composer-id>:<bubble-id>` records expose tool statuses such as
`loading` and `completed`. A controlled live run observed new running tool
bubbles and later completion transitions for the same IDs.

Intermediate result values can be revised when Cursor persists a completed
turn. Consumers must treat them as provisional. Waiting, genuine cancellation,
and error differentiation remain unvalidated.

The v0.1 provider therefore does not treat an individual failed or completed
tool as a terminal turn. Recent tool errors and completions remain `working`
until a terminal lifecycle event arrives. Fresh `hasPendingPlan` or
`hasBlockingPendingActions` signals map to `waiting`; stale copies are ignored.
The deck presents terminal errors as attention-required orange and uncertain
states as default gray, preserving the four-color product model.

## v0.1 live-state strategy

A live product check on July 17, 2026 confirmed that SQLite can still report the
selected session as `completed` while its agent is actively responding. The
provider therefore cannot meet the one-second live-state target from database
polling alone.

Cursor's documented lifecycle hooks expose a stable `conversation_id`,
`generation_id`, event name, and terminal status. The v0.1 service accepts
sanitized `sessionStart`, `beforeSubmitPrompt`, `stop`, and `sessionEnd` events
as an in-memory overlay on the SQLite snapshot. Prompt, response, tool, email,
and transcript content is discarded before transmission.

Cursor does not document whether a hook `conversation_id` equals the
`composerHeaders.composerId` stored in SQLite. elChango applies a hook signal
only when those identifiers match exactly. A focused live test is still
required before hook-backed state can be considered validated.

## M0.5: best-effort focus

POC: `scripts/poc/05_cursor_best_effort_focus.py`

Current verdict: `FOCUS_VERIFIED` for one deliberately timed rank-2 switch and
an earlier two-way scenario.

Cursor does not expose a supported deep link for opening a local agent by
composer ID. The tested undocumented `/agent?composerId=...` route had no
effect. `cursor --reuse-window` is unsafe for this purpose because it can offer
to cancel running agents before replacing the current workspace.

The Agents Window supports a Control+Tab switcher, and Cursor persists a
matching recently viewed order in `composerHeaders.recency`. Early synthetic
keyboard tests were inconsistent because key-down and key-up events were sent
too quickly:

- two Tab events toward persisted rank 2 selected rank 1;
- three Tab events later selected a session at persisted rank 6;
- an instrumented run captured identical full recency snapshots before and
  after the switch.

A physical Control+Tab selected persisted rank 1. After synthetic Tab events
were changed to 100 ms key presses with pauses, two presses selected and
verified the exact persisted rank-2 target. The POC activates Cursor, refreshes
selection and the full recency table, and performs a second preflight read
immediately before sending keys. It aborts if either value changed.

Post-action verification is still mandatory. It detects a wrong target but
does not prevent an incorrect session from briefly receiving focus.

## v0.1 targeting strategy

The Agents Window exposes direct `Cmd+1` through `Cmd+9` shortcuts in the same
logical order as its pinned and repository-grouped sidebar. For later sessions,
`Cmd+9` followed by repeated `Option+Down` continues through that order.

The v0.1 focus controller reconstructs the current sidebar order from Cursor's
settings immediately before keyboard injection, sends one shortcut sequence,
and verifies the exact selected composer ID afterward. It accepts a stale web
snapshot only when the requested session is still a focusable button in the
current deck snapshot.

## M0.8: native New Agent launch

POC: `scripts/poc/08_cursor_new_session.py`

Current verdict: `NO_NEW_COMPOSER`.

Cursor 3.12.17 exposes `glass.newAgentFromKeyboard` as `Cmd+N` in the Agents
Window. A controlled test activated Cursor, sent `Option+Cmd+N` to focus the
Agents Window, and sent `Cmd+N` exactly once. Cursor changed
`cursor/glass.selectedAgent` to `null`, but no new top-level `composerHeaders`
row appeared before the 5-second timeout plus settling period.

The shortcut likely opens an unpersisted blank New Agent view. Because no
composer ID exists before a prompt, elChango cannot verify the launched target
or add it to the deck. Product launch remains disabled, and the POC must not
retry automatically after an ambiguous result.

## Open questions

- Do composer IDs survive Cursor restarts?
- Which events change `lastUpdatedAt`, visibility timestamps, or both?
- Can an open session be distinguished reliably from an unarchived historical
  session?
- Does a hook `conversation_id` always equal its SQLite `composerId`?
- How does the schema behave across Cursor upgrades?
- Does `composerHeaders.recency` continue to match the switcher across larger
  and mixed local/cloud session sets?
- Can keyboard focus inside the target agent input be verified before
  dispatching a privileged action?

## Safety constraints

- Open Cursor databases in strict read-only mode.
- Never treat one undocumented field as authoritative without a live test.
- Keep active-session detection separate from inventory until its signals are
  proven.
- Do not dispatch actions until focus can be verified well enough to avoid
  targeting the wrong session.
