# elChango Agent Guide

## Purpose

elChango aims to monitor and control native, local AI coding agent sessions from
pluggable surfaces such as a web deck, Stream Deck hardware, and mobile.

The initial target is Cursor on macOS. The project is currently in technical
reconnaissance, before its core architecture is fixed.

## Sources of truth

Use these sources according to their role:

1. `AGENTS.md` contains durable project-wide working instructions.
2. The Cursor canvas `elchango-roadmap.canvas.tsx` contains the validated roadmap.
3. `scripts/poc/` contains executable evidence from technical reconnaissance.
4. `docs/providers/` contains provider-specific understanding and findings.

## Working agreement

- Discuss and validate development points with the user before implementing them.
- Keep the roadmap limited to decisions and development points validated together.
- Record durable project-wide directives in this file.
- Record provider-specific findings in `docs/providers/<provider>.md`, not here.
- Prefer evidence from a focused experiment over premature abstractions.
- Keep provider, surface, and bridge contracts minimal until experiments justify
  them or a second adapter demonstrates the need.
- Treat actions against agent sessions as privileged. Do not enable an action
  until the intended target can be verified well enough to prevent acting on the
  wrong session.
- Use [Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/)
  for every commit message and pull request title, including a valid type,
  optional scope, and concise description such as
  `feat(web): add the fixed deck grid`.

## POC standard

Every reconnaissance deliverable must be a self-documenting Python POC under
`scripts/poc/`. A separate report is not a substitute for the executable POC.

Each POC must:

- use a numbered, descriptive filename such as
  `scripts/poc/01_cursor_session_inventory.py`;
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

Start with M0, a Cursor feasibility phase, before building the web deck or fixing
the final architecture.

M0 investigates:

1. session inventory and identifier stability;
2. session to workspace mapping;
3. hook events and state transitions;
4. active-session detection;
5. best-effort focus;
6. session launch;
7. safe action dispatch after target verification.

Only after M0 should the project define durable contracts and build the first
vertical slice using a real Cursor session.

## Provider documentation

Cursor findings are documented in `docs/providers/cursor.md`.
