# elChango Agent Guide

## Purpose

elChango aims to monitor and control native, local AI coding agent sessions from
pluggable surfaces such as a web deck, Stream Deck hardware, and mobile.

The current native macOS host supports Cursor and Claude Code sessions opened
by Claude Desktop. Provider behavior remains evidence-driven and must degrade
conservatively when undocumented integrations change.

## Sources of truth

Use these sources according to their role:

1. `AGENTS.md` contains durable project-wide working instructions.
2. `scripts/poc/<provider>/` contains executable evidence from technical
   reconnaissance.
3. `docs/providers/` contains provider-specific understanding and findings.

## Working agreement

- Discuss and validate development points with the user before implementing them.
- Keep the roadmap limited to decisions and development points validated together.
- Record durable project-wide directives in this file.
- Record provider-specific findings in `docs/providers/<provider>.md`, not here.
- Prefer evidence from a focused experiment over premature abstractions.
- Keep provider, surface, and bridge contracts minimal until experiments justify
  them or a second adapter demonstrates the need.
- Keep session identity structured as provider ID plus native session ID.
  Surfaces may receive the provider-qualified ID as a local routing value, but
  it is never an authorization capability.
- Resolve privileged actions from a freshly rebuilt button, then dispatch the
  native ID through the matching provider adapter and preserve exact
  post-action verification.
- Treat actions against agent sessions as privileged. Do not enable an action
  until the intended target can be verified well enough to prevent acting on the
  wrong session.
- Use [Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/)
  for every commit message and pull request title, including a valid type,
  optional scope, and concise description such as
  `feat(web): add the fixed deck grid`.

## POC standard

Every reconnaissance deliverable must be a self-documenting Python POC under
`scripts/poc/<provider>/`. Each provider has its own sequence beginning at
`01`. A separate report is not a substitute for the executable POC.

Each POC must:

- use a numbered, descriptive filename such as
  `scripts/poc/cursor/01_cursor_session_inventory.py`;
- run directly and expose useful `--help` documentation;
- explain its purpose, method, safety properties, interpretation, and limitations;
- print observable evidence and a conservative verdict;
- offer machine-readable output when it is useful;
- avoid writes and side effects unless the experiment explicitly requires and
  documents them;
- prefer the Python standard library unless a dependency is clearly justified;
- fail clearly when an undocumented external schema no longer matches;
- distinguish observations from conclusions and unproven assumptions.

## Validated launch sequence

For a new provider, begin with a feasibility phase before adding it to shared
surfaces or extending durable contracts.

M0 investigates:

1. session inventory and identifier stability;
2. session to workspace mapping;
3. hook events and state transitions;
4. active-session detection;
5. best-effort focus;
6. session launch;
7. safe action dispatch after target verification.

Only after provider feasibility is established should the project extend
durable contracts and build a vertical slice using a real session.

## Provider documentation

Cursor findings are documented in `docs/providers/cursor.md`. Claude Code
findings are documented in `docs/providers/claude-code.md`.
