# Security policy

elChango is a local control surface with macOS Accessibility permission. Treat
it as privileged software: it can focus supported coding agents, inject a
small set of fixed commands, and ask an agent to perform operations such as
committing and pushing.

## Supported versions

Security fixes are provided for the current stable release line and `main`.
Users should update to the latest patch release before reporting a problem.

| Version | Supported |
| --- | --- |
| `1.x` | Yes |
| `main` | Yes |
| `< 1.0` | No |

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability. Use
[GitHub private vulnerability reporting](https://github.com/jychp/elchango/security/advisories/new)
and include:

- the affected commit or version;
- the provider and surface involved;
- sanitized reproduction steps;
- the security impact and required preconditions;
- any suggested mitigation.

Never include prompts, responses, source code, credentials, session content,
the contents of `control-token`, or other private data. The maintainer will
respond on a best-effort basis and coordinate disclosure after a fix is
available.

Security problems in Cursor, Claude Code, macOS, or Stream Deck should also be
reported to the relevant vendor.

## Local threat model

### Protected boundaries

The current design aims to protect privileged API operations from:

- malicious web pages opened while elChango is running;
- processes belonging to another macOS user;
- accidental or unauthenticated loopback clients;
- stale, ambiguous, or malformed surface requests;
- provider schema changes and uncertain session identity.

The service binds only to `127.0.0.1`. Loopback binding alone is not considered
authorization.

### Same-user process limitation

A malicious process already running as the same macOS user is outside the
current protection boundary. It can read files owned by that user, including
the control token, and may already have broad access to local development
data. The token is not presented as a sandbox against same-user malware.

Root compromise, physical compromise, a compromised macOS account, and
compromise of Cursor or Claude Code themselves are also outside this model.

## Loopback API authorization

On first launch, the app generates a 256-bit random control token at:

```text
~/Library/Application Support/elChango/control-token
```

The `elChango` directory is restricted to the current user (`0700`) and the
token file is restricted to its owner (`0600`). The app rejects symbolic links,
unexpected ownership, unsafe permissions, and malformed existing tokens. The
token is never included in API responses, diagnostics, logs, or repository
files.

The Stream Deck plugin reads this file and sends the token as a Bearer
credential. The web deck never reads the persistent token. Choosing **Open Web
Deck** from the app creates a short-lived, single-use bootstrap URL. The
service exchanges it for an in-memory browser session using an `HttpOnly`,
`SameSite=Strict` cookie and immediately redirects to a clean URL.

The service also:

- requires the expected `Host` header;
- rejects foreign browser `Origin` values;
- requires authentication for health, snapshot, activation, and long-press
  routes;
- rejects stale command revisions and rebuilds the target snapshot before
  acting;
- limits request body sizes.

To rotate the token, quit elChango, delete `control-token`, relaunch the app,
and reopen the web deck from the menu. The Stream Deck plugin reads the current
token for each request.

## Provider hooks

Cursor and Claude hooks use separate nonprivileged endpoints. Claude's official
HTTP-hook configuration does not provide a safe way to distribute the
per-install control token, so hook endpoints do not accept privileged actions
and do not inherit API authorization.

Hook defenses are deliberately layered:

- browser requests with foreign origins are rejected;
- request bodies are bounded and decoded into an allowlisted payload;
- prompt text, responses, tool content, notification messages, email, and
  transcript content are discarded;
- a hook can affect state only when its native session ID exactly matches the
  current persistent provider inventory;
- requests are rate-limited independently per provider;
- invalid or unavailable providers fail without blocking other providers.

A same-user process can still spoof lifecycle metadata for a known session.
Such a hook may change a displayed state, but it cannot directly dispatch a
command or bypass the fresh target verification required for an action.

## Provider data access

elChango does not write to Cursor or Claude Code data stores:

- Cursor SQLite databases are opened read-only and use
  `PRAGMA query_only=ON`;
- Claude Code records and transcripts are read from the filesystem without
  modification;
- provider schema mismatches fail explicitly instead of being guessed;
- transcript reads are bounded and stop after the metadata required for
  inventory mapping is found.

elChango stores only its own preferences, control token, and in-memory
lifecycle observations. It has no telemetry service and does not send provider
content to a remote elChango server.

Read-only provider discovery does not make actions read-only. A verified
semantic command can instruct an agent to change code, create a pull request,
commit, or push.

## Accessibility and action safety

Accessibility permission is used to inspect the frontmost supported
application, verify provider-specific UI targets, focus sessions, and dispatch
fixed shortcuts or text recipes. Surface clients cannot submit arbitrary
commands, shortcuts, scripts, or prompt strings.

Before a privileged action, elChango conservatively checks the
provider-qualified session, frontmost provider, uniquely selected target,
declared capability, and provider-specific command target. Text recipes also
require the expected empty composer. Actions are serialized process-wide and
are never retried after an ambiguous dispatch.

`accepted` means that dispatch was verified. It does not prove that the coding
agent completed the requested semantic operation.

## Release integrity

Official macOS release archives are Universal 2 applications signed with a
`Developer ID Application` certificate, Hardened Runtime, and an Apple trusted
timestamp. The release workflow submits each app to Apple's notary service,
requires an `Accepted` result, staples the ticket, and verifies Gatekeeper
acceptance before publication.

GitHub stores the certificate and App Store Connect API key only as protected
`release` environment secrets. The workflow imports them into an ephemeral
keychain and removes reconstructed key material in an unconditional cleanup
step. Release artifacts include SHA-256 checksum files.

Development builds use an available Apple Development or Developer ID identity
when possible, then fall back to ad hoc signing. They are not official
distribution artifacts. See [docs/releasing.md](docs/releasing.md) for the
release process, secret rotation, and independent verification commands.

## Known limitations

- Text input currently uses the global HID event tap after verification because
  Electron webviews did not accept PID-targeted Unicode events in tested
  versions. A foreground-window change during dispatch remains a residual race.
- Cursor and Claude integrations depend on undocumented local schemas,
  Accessibility markers, and native shortcuts that can change between vendor
  releases.
- Provider hooks authenticate session identity through current inventory but
  are not cryptographically authenticated.
- The fixed Cursor hook path trusts the application installed at
  `/Applications/elChango.app`. Install the app only from a source you trust.
- Development builds are not notarized public distribution artifacts.
- Provider-qualified session IDs and diagnostic source paths are local
  identifiers, not secrets or opaque authorization capabilities.

Detailed provider evidence and tested limitations are maintained in
[docs/providers/cursor.md](docs/providers/cursor.md) and
[docs/providers/claude-code.md](docs/providers/claude-code.md).

## Safe development and disclosure

- Use synthetic or sanitized fixtures for tests and POCs.
- Never commit provider databases, transcripts, prompts, tokens, credentials,
  or private workspace paths.
- Live POCs must remain opt-in and document their side effects.
- Treat new commands, hooks, focus mechanisms, and session identifiers as
  security-sensitive changes requiring negative tests.
