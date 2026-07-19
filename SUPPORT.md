# Support

elChango is a community-maintained open source project. Support is provided on
a best-effort basis and no response time or resolution is guaranteed.

## Start here

1. Read the [README troubleshooting section](README.md#troubleshooting).
2. Open the app's **Diagnostics** menu and note the version, service,
   Accessibility, and provider status.
3. Check existing
   [GitHub issues](https://github.com/jychp/elchango/issues) for the same
   behavior.
4. Confirm the problem still occurs with a supported elChango release and
   current provider software.

## Ask for help or report a bug

Use the repository's
[GitHub issue forms](https://github.com/jychp/elchango/issues/new/choose).
Choose a bug, feature, provider, or command report as appropriate.

Include:

- the elChango version or commit;
- macOS, provider, browser, and Stream Deck versions that apply;
- the affected surface;
- concise reproduction steps;
- expected and observed behavior;
- sanitized diagnostics or error messages;
- for action failures, how the intended provider and session were identified
  and what happened after dispatch;
- the checks or workarounds already attempted.

Do not include prompts, responses, private source code, credentials, control
tokens, provider databases, full transcripts, personal workspace paths, or
other sensitive session content.

## Supported scope

Project support covers:

- installation of official elChango releases;
- the macOS menu bar app and bundled browser deck;
- official Cursor, Claude Code, and Stream Deck plugins;
- documented session inventory, state, focus, launch, personalization, and
  semantic commands;
- reproducible regressions in supported versions.

The project cannot provide:

- general Cursor, Claude Code, macOS, Homebrew, or Stream Deck support;
- recovery or debugging of private agent conversations or repositories;
- custom provider integrations or arbitrary command recipes on demand;
- guarantees for undocumented provider formats after a vendor update;
- support for modified, unsigned, or third-party distributions;
- private consulting or guaranteed response times.

When an external product is the source of a problem, report it to that vendor.

## Security and conduct

Do not report vulnerabilities in a public issue. Follow the private disclosure
process in [SECURITY.md](SECURITY.md).

Report community conduct concerns privately as described in
[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

## Development questions

Contributor setup, architecture, builds, tests, and plugin development are
documented in [docs/dev.md](docs/dev.md). Proposed changes must follow
[CONTRIBUTING.md](CONTRIBUTING.md).
