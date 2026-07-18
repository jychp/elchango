---
name: new-command
description: Adds or researches a personalized agent command in elChango using stable semantic IDs, provider-specific evidence, exact session and command-target verification, one-shot POCs, and conservative dispatch verdicts. Use when adding accept, create PR, commit and push, compact, or another privileged session command.
---

# Add a personalized command

Treat every command as a privileged action. A focused application is not enough:
prove the exact session and the provider-specific command target before
dispatching anything.

## 1. Validate scope with the user

Before editing product code, agree on:

- the user intent and provider;
- the stable provider-neutral semantic ID;
- which official provider evidence is available;
- whether a real-session dispatch experiment is safe now;
- the expected post-dispatch evidence.

The initial stable IDs are:

- `accept`
- `create_pr`
- `commit_push`
- `compact`

Do not rename these per provider or surface. Do not add a new ID merely because
one provider exposes a command. Add a shared ID only after the user validates
its provider-neutral meaning.

## 2. Separate semantic intent from recipes

A semantic ID states what the user intends. A provider recipe states how one
provider may perform it.

Recipes can be:

- an officially documented provider command or shortcut;
- an explicitly configured natural-language instruction;
- an explicitly configured provider command under reconnaissance.

Do not invent a command mapping from memory, UI labels, autocomplete, another
provider, or an undocumented string found in product files. Cite official
evidence when claiming a mapping is official. When evidence is unavailable,
require recipe text or command through an explicit local CLI option.

Surfaces emit only the stable semantic ID and target button identity. Web,
Stream Deck, mobile, and other surfaces must never send arbitrary prompt text,
slash commands, keyboard shortcuts, shell commands, or scripts.

## 3. Build the POC first

Create a numbered, self-documenting standard-library Python POC under
`scripts/poc/<provider>/` before defining a product contract or adapter method.
Continue that provider directory's independent sequence. Follow the POC
standard in `AGENTS.md`.

The POC must:

1. default to read-only or dry-run;
2. require explicit `--execute` for any input injection;
3. accept only known semantic IDs;
4. require an explicit provider recipe when no official mapping is proven;
5. identify one exact provider-native session;
6. verify the intended session is currently selected;
7. verify the matching provider application is frontmost;
8. verify the exact agent prompt input for text recipes, or the proven
   application-level shortcut scope and command eligibility for shortcut
   recipes;
9. repeat all volatile checks immediately before injection;
10. inject at most once;
11. avoid coordinate clicks, broad paste targets, fallbacks, and retries;
12. print observable evidence and a conservative verdict;
13. offer JSON output when useful.

Explain why selected-session and frontmost-application evidence alone cannot
distinguish an agent prompt from a terminal, editor, search field, title field,
or other text input.

## 4. Establish exact target evidence

Resolve the target from a freshly rebuilt button to:

```text
public provider-qualified ID
  -> current provider
  -> current provider-native session ID
  -> current inventory record
```

Reject missing, archived, draft, ephemeral, subagent, duplicated, or otherwise
ineligible targets. Never use a title, repository name, workspace path, screen
position, or stale surface index as session identity.

Immediately before every dispatch, prove:

- the provider's authoritative selected-session signal equals the target;
- the provider application is frontmost;
- provider-specific evidence uniquely identifies the intended command target.

For text recipes, also prove that the focused accessibility element belongs to
that application, is enabled, accepts text, and has provider-specific evidence
that uniquely identifies it as the agent prompt. A generic `AXTextArea`, DOM
text box, or editable role is insufficient.

An application-level shortcut may omit prompt-input verification only when
focused-input state is irrelevant to the measured provider behavior and the
shortcut mapping has been explicitly validated for that semantic command. It
must still require exact selected-session, foreground-application, and
command-eligibility evidence. Do not generalize this exception to text recipes.

## 5. Dispatch once

After a matching second preflight:

1. resolve the recipe from trusted provider configuration;
2. inject that exact recipe;
3. submit once if submission is part of the measured recipe;
4. gather post-dispatch evidence without sending more input;
5. stop regardless of outcome.

Never try a second recipe, resend after a timeout, switch to a shortcut, click a
fallback location, or refocus and retry. An ambiguous first result may already
have changed the conversation.

## 6. Use conservative verdicts

Keep target verification, input delivery, provider acceptance, and semantic
completion separate.

Suitable verdict classes include:

- `DRY_RUN`
- `READY`
- `TARGET_NOT_SELECTED`
- `INPUT_FOCUS_NOT_VERIFIED`
- `STALE_PREFLIGHT`
- `DISPATCH_SENT`
- `POST_DISPATCH_AMBIGUOUS`

`DISPATCH_SENT` means only that one recipe was submitted under verified
preconditions. It does not mean the provider understood, accepted, completed,
committed, pushed, created a pull request, compacted context, or approved a
pending action.

Claim semantic completion only from provider-specific evidence that uniquely
correlates the result with the target and attempted dispatch.

## 7. Document observations separately

Update `docs/providers/<provider>.md` with:

- exact observed version and environment;
- official references;
- measured target, foreground, and command-target signals, including input
  focus when the recipe types text;
- recipe source and whether it is official or operator supplied;
- dry-run and execute evidence;
- verdicts and limitations;
- unproven command mappings and open questions.

Keep observations, conclusions, and hypotheses separate. Do not turn a
successful keystroke into a claim of official support.

## 8. Define product contracts only after evidence

Do not edit backend, bridge, web, Stream Deck, mobile, or tests until the user
reviews the POC evidence and validates the development point.

When implementation is authorized:

- keep the shared contract limited to stable semantic IDs;
- resolve provider recipes inside trusted backend configuration;
- never place recipe strings in button models or surface intents;
- rebuild target identity immediately before every privileged action;
- expose capability per provider and, when needed, per session;
- preserve exact preflight and conservative post-dispatch evidence;
- audit semantic ID, provider, target, surface, verdict, and timing without
  recording prompt or conversation content.

Do not add provider-specific branches to shared surfaces or the deck service.

## 9. Verify

For reconnaissance-only work, run:

```bash
python3 -m py_compile scripts/poc/<provider>/<number>_<provider>_command_dispatch.py
python3 scripts/poc/<provider>/<number>_<provider>_command_dispatch.py --help
git diff --check
```

Also verify:

- the POC performs no dispatch without `--execute`;
- execute mode rejects absent target, recipe, and input-focus evidence;
- each attempt has one injection path and no retry;
- provider docs label mappings as observed, official, configured, or unproven;
- this skill remains under 500 lines.

Do not commit or push unless the user explicitly asks.
