## Summary

<!-- Explain the user-visible outcome and why this change is needed. Do not include sensitive prompt or session content. -->

## Evidence

<!-- Link issues and sanitized observations, and cite the evidence recorded in docs/providers/<provider>.md. Distinguish observations from assumptions. -->

## Test plan

- [ ] Relevant tests pass locally.
- [ ] macOS Swift tests/build were run, or are not applicable.
- [ ] Web checks/build were run, or are not applicable.
- [ ] Cursor, Claude, and Codex provider plugin validation/build were run, or are not applicable.
- [ ] Stream Deck checks/build were run, or are not applicable.

## Provider and command safety

- [ ] New provider behavior is documented with evidence in `docs/providers/<provider>.md` (including its feature-coverage table) and backed by versioned fixtures under `contracts/providers/`, or is not applicable.
- [ ] Privileged actions rebuild and freshly verify the intended provider-qualified target immediately before dispatch, or are not applicable.
- [ ] The provider-specific command target is verified, including input focus for text recipes; post-dispatch evidence is reported conservatively and ambiguity fails closed, or is not applicable.
- [ ] Evidence and test data exclude prompts, credentials, private code, and personal session content.

## Surface parity

- [ ] Web and Stream Deck behavior, labels, disabled states, and feedback remain aligned, or any intentional difference is explained below.

<!-- Explain intentional parity differences or other review notes. -->
