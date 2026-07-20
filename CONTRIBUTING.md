# Contributing to elChango

Thank you for helping improve elChango. Contributions should solve a clear user
or maintainer problem, preserve conservative action safety, and be easy to
review.

## Before you start

- Read the [development guide](docs/dev.md) for architecture, setup, tests,
  builds, plugin development, and package validation.
- Search existing issues before opening a new one.
- Use the issue forms for bugs, features, providers, and commands.
- Discuss substantial changes before implementation. Do not expand durable
  contracts or the roadmap based only on an unvalidated idea.
- Report vulnerabilities privately according to [SECURITY.md](SECURITY.md).

## Development workflow

1. Create a focused branch.
2. Make the smallest change that proves the intended behavior.
3. Add or update tests that would fail without the change.
4. Run the relevant focused checks, then run `make` when practical.
5. Review the diff for unrelated formatting, generated files, sensitive data,
   and unsupported claims.
6. Open a pull request using a
   [Conventional Commit](https://www.conventionalcommits.org/en/v1.0.0/)
   title, such as `fix(cursor): reject an ambiguous target`.

Every pull request must describe the user-visible outcome, evidence, and test
plan. For provider behavior, include sanitized reproduction steps and cite the
evidence recorded in `docs/providers/<provider>.md`. Never include prompts,
credentials, tokens, private source code, provider databases, or personal
session content.

## Quality and anti-slop rules

AI-assisted contributions are welcome only when the contributor understands,
tests, and takes responsibility for every line. Generated-looking filler is not
acceptable.

- **Tests are required.** Behavior changes need focused automated tests. If a
  test is genuinely impossible, explain why and provide a repeatable manual
  check.
- **Prove that it functions.** A clean build is not proof of behavior. Include
  the relevant test result, sanitized runtime observation, fixture, or focused
  experiment.
- **Comments explain why only.** Do not narrate syntax, restate names, or add
  tutorial prose inside production code.
- **Code must be self-documenting.** Prefer precise names, small units, and
  explicit contracts over explanatory clutter.
- **Keep diffs minimal.** Do not mix drive-by refactors, formatting churn,
  speculative abstractions, or unrelated dependency updates into a change.
- **Do not fabricate APIs.** Verify symbols, schemas, shortcuts, hooks, product
  behavior, and version requirements against source, official documentation,
  or executable evidence.
- **Undocumented integrations are evidence-driven.** For provider internals,
  record observations, evidence, limitations, and uncertainty in
  `docs/providers/<provider>.md`, and back them with versioned fixtures under
  `contracts/providers/`. Ambiguity must degrade conservatively.
- **Do not add generated-looking filler.** Reject redundant headings, empty
  wrappers, placeholder tests, obvious comments, generic error handling,
  speculative compatibility layers, and prose that claims more than the
  evidence supports.

Maintainers may close contributions that add code volume without demonstrated
value or that require reviewers to reconstruct whether the change works.

## Provider and action safety

Actions against sessions are privileged. A change must not enable an action
until it can verify the intended provider-qualified session and the exact
provider-specific target.

- Rebuild the requested button from current state before dispatch.
- Treat button IDs and native session IDs as routing values, not authorization
  capabilities.
- Keep arbitrary prompts, shortcuts, scripts, and shell commands out of surface
  contracts.
- Verify input focus for text recipes.
- Serialize privileged dispatch and do not retry ambiguous actions.
- Report post-action evidence exactly. Accepted dispatch does not prove that an
  agent completed the semantic operation.
- Fail closed on stale revisions, schema drift, ambiguous identity, and failed
  Accessibility checks.

For new or changed provider behavior, follow the evidence standard in
[docs/dev.md](docs/dev.md) and the durable project rules in
[AGENTS.md](AGENTS.md).

## Documentation and style

- Write US English.
- Do not use em dashes or en dashes as punctuation.
- Keep user documentation outcome-focused and distinguish observations from
  assumptions.
- Update public documentation when installation, permissions, privacy,
  commands, compatibility, or troubleshooting changes.
- Preserve the distinction between the MIT-licensed software and documentation
  and the reserved elChango branding described in
  [TRADEMARKS.md](TRADEMARKS.md).

## Checks

Run the checks relevant to your change. The complete suite is:

```bash
make
```

Focused targets and package commands are listed in
[docs/dev.md](docs/dev.md). In the pull request, state exactly what you ran and
what you did not run.

## Community standards

Participation is governed by [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md). Support
expectations are documented in [SUPPORT.md](SUPPORT.md).

By contributing, you agree that your contribution is licensed under the
[MIT License](LICENSE). The license does not grant rights to use reserved
project branding for a derived product.
