# Security model

This document describes elChango's runtime trust boundaries. Vulnerability
reporting, supported versions, release integrity, and disclosure guidance are
in the root [security policy](../SECURITY.md).

## Trust boundary

elChango protects privileged loopback actions from malicious web pages, other
macOS users, accidental unauthenticated clients, stale requests, malformed
payloads, and ambiguous provider targets. A malicious process already running
as the same macOS user is outside this boundary because it can read that
user's files, including the control token.

The service listens only on `127.0.0.1:8765`. It additionally enforces the
expected `Host` and browser `Origin`, authenticates health, snapshot,
activation, and long-press routes, bounds request bodies, and rejects stale
deck revisions.

## Local authorization

The app creates a 256-bit random token at:

```text
~/Library/Application Support/elChango/control-token
```

The directory is owner-only (`0700`) and the token is owner-readable and
owner-writable (`0600`). Symbolic links, unexpected ownership, unsafe
permissions, and malformed tokens are rejected.

Stream Deck reads the token locally and sends Bearer authentication. The web
deck never reads it. **Open Web Deck** issues a short-lived, single-use
bootstrap URL that becomes an in-memory `HttpOnly`, `SameSite=Strict` session
cookie before redirecting to a clean URL.

Stable and debug builds share this token, port, API, and plugins so clients
always address the one active service. They cannot run concurrently. Their
bundle IDs, Accessibility identities, app names, and preference files remain
distinct.

## Provider data

Provider discovery is read-only:

- Cursor SQLite databases use read-only mode and `PRAGMA query_only=ON`.
- Claude Code records and transcripts are read without modification.
- Transcript reads are bounded to the metadata needed for inventory mapping.
- Schema mismatches fail explicitly instead of being inferred.

Hooks discard prompt text, responses, tool content, notification messages,
email, and transcript content. Hook payloads can update bounded lifecycle
state only after their native session ID matches current persistent inventory.
Hooks cannot invoke privileged actions.

Read-only discovery does not make actions read-only. A verified command can
ask a coding agent to modify code, create a pull request, commit, or push.

## Privileged actions

Before dispatch, elChango rebuilds the requested button and verifies:

1. the provider-qualified session identity;
2. the current native session identity;
3. the frontmost provider application;
4. the uniquely selected target;
5. the declared capability;
6. the provider-specific command or empty input target.

Privileged actions are serialized process-wide and are not retried after an
ambiguous dispatch. An `accepted` response means dispatch was verified. It
does not prove that the agent completed the semantic operation.

## Residual risks

- Cursor and Claude Code integrations depend on undocumented local schemas,
  Accessibility markers, and native shortcuts that vendors can change.
- Text input uses the global HID event tap after target verification because
  tested Electron webviews rejected PID-targeted Unicode events. A foreground
  application change during dispatch remains a race.
- Same-user processes can spoof bounded hook state for a known session.
- Provider-qualified session IDs and diagnostic paths are local routing
  values, not secrets or authorization capabilities.

Provider-specific evidence and limitations are documented in
[providers/cursor.md](providers/cursor.md) and
[providers/claude-code.md](providers/claude-code.md).
