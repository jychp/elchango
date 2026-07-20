# Provider feature matrix and consistency audit

This document compares the shipped harness providers (Cursor, Claude Code, and
Codex Desktop) side by side. It has two parts: the feature-coverage matrix, and
a cross-provider consistency review of how each provider implements the shared
`AgentProvider` boundary. Per-provider behavior and limitations live in the
individual docs: [cursor.md](cursor.md), [claude-code.md](claude-code.md), and
[codex.md](codex.md). Each of those opens with its own feature table.

Status legend: `✅ supported`, `⚠️ partial` (implemented with a behavioral
limitation, noted inline), `❌ not supported`.

## Feature coverage

All three providers declare the same descriptor capabilities
(`[.focusSession, .newSession, .executeCommand]`) and the same command set
(`accept`, `create_pr`, `commit_push`, `compact`). The differences are in how
each capability is implemented.

| Feature | Cursor | Claude Code | Codex |
| --- | --- | --- | --- |
| Sessions inventory | ✅ SQLite `state.vscdb`, schema-validated, strict read-only | ✅ Metadata-prefix parse of `local_*.json` + 1:1 transcript correlation | ✅ Rollout `*.jsonl` scan, filtered to Desktop/user threads |
| Session live status | ✅ DB inference merged with lifecycle hooks | ✅ Hook-driven; idle when no hook signal | ✅ Hook-driven; idle when no hook signal |
| Session focus | ✅ AX + sidebar `Cmd+1`..`Cmd+9`, verifies selected id + frontmost | ✅ Sidebar shortcut from persisted config order, stale-preflight | ✅ id-addressed `codex://threads/<id>` deep link, verifies foreground |
| Session creation | ⚠️ `Cmd+N` opens a blank New Agent view Cursor does not persist, so it cannot be tracked on the deck | ✅ `claude://code/new` deep link | ✅ `codex://threads/new` deep link |
| Commands | ✅ Recipes + composer marker, `unfocusedPolicy: .reject` | ✅ Recipes, `AXGroup` fallback exception | ⚠️ Typed dispatch with no input-target marker; acts on the frontmost window and the user confirms the result |

Reading the matrix:

- **Inventory** is solid on all three; each reads a different persistent store.
- **Live status** is hook-driven on all three; a session with no hook signal is
  idle. Cursor additionally merges persisted DB inference (see divergence 5).
- **Focus** is supported everywhere but by very different mechanisms (see
  divergence 3).
- **Session creation** creates a session everywhere; on Cursor the created view
  is not persisted, so it cannot be tracked on the deck.
- **Commands** dispatch on all three. Cursor and Claude verify the composer
  input target; Codex types into the frontmost window without an input-target
  marker, so the user confirms the result.

## Cross-provider consistency ("way of doing things")

The shared boundary is the `AgentProvider` protocol in
`macos-app/Sources/ElChangoCore/Models/ProviderModels.swift`, consumed
polymorphically by `DeckService`. Alignment across providers is an explicit
design goal; the three implementations meet it only partially. Each divergence
below is documented with a proposed follow-up. These are recommendations, not
changes made in this pass.

### 1. Selected-session determination differs three ways

- Cursor: authoritative `cursor/glass.selectedAgent` DB key, uniqueness-checked.
- Claude Code: newest unique `lastFocusedAt` timestamp.
- Codex: in-memory `lastFocusedNativeID` (no static signal exists), reported as a
  persistent proxy. It is set on a verified focus and kept across snapshots for as
  long as that session remains in the inventory, dropped only when the session
  disappears; it is not gated on Codex being frontmost. It can go stale if the
  user switches threads inside Codex.

This is the largest structural inconsistency. Proposed follow-up: define one
selected-session contract with a documented confidence level, and let each
provider report against it (authoritative / inferred / focus-tracked), so
`DeckService` and the deck highlight treat selection uniformly.

### 2. Per-session capability computation differs

- Cursor: full capability set for every session, unconditionally.
- Claude Code: downgrades sessions absent from the sidebar order to
  `[.executeCommand]` only.
- Codex: hardcodes `[.focusSession, .executeCommand]`, omitting `.newSession`
  per session even though the descriptor advertises it.

Proposed follow-up: agree on one rule for when `focusSession` is offered per
session, and derive `newSession` consistently (it is provider-level, so it does
not belong in the per-session set on any provider).

### 3. Focus mechanism and verification strength are uneven

Cursor and Claude both use positional sidebar keyboard shortcuts with near
duplicated keycode maps and shortcut logic in `CursorProvider.swift` and
`ClaudeCodeProvider.swift`. Codex uses a clean id-addressed deep link but can
only verify foreground, not selection.

Proposed follow-up: extract the shared sidebar-keycode map and
`send*SidebarShortcut` logic into one helper reused by Cursor and Claude.

### 4. Action-result shape is inconsistent

The `actionResult` timing helper (elapsed-ms computation) is duplicated verbatim
in Cursor and Claude. Codex builds `ProviderActionResult` inline without the
helper, so its results omit the `executed` / `elapsed_ms` fields the other two
carry.

Proposed follow-up: promote the helper to a shared extension and have Codex use
it, so every provider returns the same result shape.

### 5. State-derivation philosophy differs

Cursor mixes persisted DB inference with hooks (a ~190-line `inferState`), so it
can report a meaningful state with zero hooks. Claude and Codex are hook-only
with a static idle default. The new-harness guidance states that live state
comes only from hooks and that `working`/`waiting`/`done` must never be derived
from persisted files. Cursor's DB inference is the documented exception and the
one provider that diverges from that rule.

Proposed follow-up: decide whether Cursor's DB inference should be narrowed to
ordering-only (matching Claude/Codex) or whether the rule should explicitly
sanction a per-provider inference path; either way, make the intent uniform.

### 6. Activity stores duplicate structure

`CursorActivityStore`, `ClaudeActivityStore`, and `CodexActivityStore` in
`ActivityStores.swift` are ~90% structurally identical but share no base; the
hook key fields differ by harness (Cursor keys off `conversation_id`, the others
off `session_id`; Cursor validates `composerMode`, the others `permissionMode`).

Proposed follow-up: extract a shared activity-store base with a small
per-provider adapter for the differing key/validation fields.

## How to keep this current

Update the matrix whenever a provider's feature status changes, and add a
divergence row when a new provider (or a change to an existing one) introduces a
cross-provider inconsistency. When a proposed follow-up is implemented, remove
its row here and reflect the aligned behavior in the affected provider docs.
