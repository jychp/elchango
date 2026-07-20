# elChango Agent Guide

## Purpose

elChango aims to monitor and control native, local AI coding agent sessions from
pluggable surfaces such as a web deck, Stream Deck hardware, and mobile.

The current native macOS host supports Cursor, Claude Code, and Codex Desktop
sessions. Provider behavior remains evidence-driven and must degrade
conservatively when undocumented integrations change.

## Sources of truth

Use these sources according to their role:

1. `AGENTS.md` contains durable project-wide working instructions.
2. `docs/providers/` contains provider-specific understanding, evidence, and
   findings. It is the single source of truth for provider behavior.
3. `docs/providers/feature-matrix.md` compares the providers side by side:
   feature coverage and cross-provider consistency.

## Working agreement

- Discuss and validate development points with the user before implementing them.
- Keep the roadmap limited to decisions and development points validated together.
- Record durable project-wide directives in this file.
- Never disable or remove an existing product capability without the user's
  explicit approval.
- Never run a live provider experiment that changes application focus, session
  selection, editor contents, or agent state without the user's explicit
  approval immediately before that experiment. Static inspection, fixtures,
  and automated tests that do not touch live provider sessions remain allowed.
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

## Evidence standard

Every reconnaissance finding lives in `docs/providers/<provider>.md`. That
document is the single, self-contained source of truth for a provider; keeping
evidence there instead of in a parallel set of scripts prevents drift between
what the code does and what the docs claim.

For each capability, the provider doc must:

- state the exact observed product version and environment;
- distinguish observations from conclusions and unproven assumptions;
- record a conservative verdict per feature;
- cite official documentation, or explicitly mark evidence as absent;
- describe the safety properties and any side effects of a live experiment;
- explain how the integration fails when an undocumented external schema no
  longer matches.

Share versioned fixtures under `contracts/providers/<provider>/v1/` between the
Swift tests and the documented evidence so ordering and inventory assertions are
reproducible. Follow the required structure in
[`.agents/skills/new-harness/templates/provider-doc.md`](.agents/skills/new-harness/templates/provider-doc.md).

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
findings are documented in `docs/providers/claude-code.md`. Codex Desktop
findings are documented in `docs/providers/codex.md`. Each doc opens with a
feature-coverage table. `docs/providers/feature-matrix.md` compares the
providers side by side and tracks cross-provider consistency.
