# Changelog

All notable changes to elChango are documented in this file.

The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.0] - 2026-07-19

### Added

- Codex Desktop provider: read-only inventory of `Codex Desktop` user sessions
  from `~/.codex/sessions`, rollout-derived state, and a plugin hook path for
  live lifecycle state. Focus, new-session, and command dispatch are
  implemented but fail closed pending live verification. See
  `docs/providers/codex.md`.
- `codex` deck icon across the HTTP, web, and Stream Deck contracts.
- elChango Codex plugin (`plugins/codex`) that relays lifecycle events through
  the reporter, plus a `codex` reporter provider.

## [1.0.0] - 2026-07-18

### Added

- Native macOS menu bar host with a bundled 15-key browser deck.
- Cursor and Claude Code session inventory with shared working, waiting, done,
  error, idle, and degraded state.
- Provider plugins for bounded, fail-open lifecycle reporting.
- Verified focus for existing sessions and neutral new-session launch.
- Fixed semantic commands for Accept, Open PR, Commit Push, and Compact with
  fresh target and command-input verification.
- Persistent session icon and command-slot personalization.
- Stream Deck MK.2 plugin and generated 15-key profile with independent device
  pagination.
- Authenticated loopback control API, one-time browser bootstrap, bounded hook
  routes, and conservative failure behavior.
- Signed, notarized Universal 2 macOS distribution and Homebrew Cask install.

[1.1.0]: https://github.com/jychp/elchango/releases/tag/v1.1.0
[1.0.0]: https://github.com/jychp/elchango/releases/tag/v1.0.0
