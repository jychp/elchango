# Provider documentation template

Update `docs/providers/<provider>.md` using this required structure. Do not omit
a section. Write `None established` where evidence does not yet support content.
Do not fill gaps by inference or copy a finding from another provider.

1. **Scope and status**: define the exact product and session type, exclusions,
   implementation status, and conservative capability verdicts.
2. **Tested versions and environment**: record product version, date, operating
   system, and any relevant hardware or installation assumptions. Use
   `unknown` when a value was not captured.
3. **Evidence**: summarize the controlled observations, fixture parity, and
   official references that support the conclusions. Keep observations,
   conclusions, and hypotheses distinct.
4. **Inventory and identity**: document persistent and live sources, stable
   native IDs, filtering rules, ordering signals, and process-liveness
   annotations.
5. **Workspace mapping**: explain which fields map a session to its current
   workspace, repository, or worktree, including ambiguity and failure rules.
6. **State model and hooks**: list authoritative lifecycle signals, the mapping
   to provider-neutral states, stale-signal behavior, and retained metadata.
   Include a **handled-states table** as described below.
7. **Focus and launch**: describe measured existing-session focus and new-session
   launch mechanisms, preflight checks, post-action verification, and verdicts.
8. **Semantic commands**: list each stable semantic ID, its evidence-backed
   provider recipe, composer requirements, dispatch semantics, and unproven
   completion claims.
9. **Safety and target verification**: state the exact identity, foreground,
   command-target, bounded-dispatch, and read-only barriers. Include
   input-focus evidence for every text recipe.
10. **Degradation behavior**: explain schema drift, malformed records, missing
    harnesses, stale hooks, and provider isolation.
11. **Limitations and open questions**: preserve every unproven assumption and
    required live experiment.
12. **POCs**: list every executable evidence file under the provider's POC
    directory with its purpose and current verdict.
13. **References**: link official provider documentation and any other primary
    sources used.

## Handled states (required in the State model section)

Every provider must document which `SessionState` values it handles and how each
one is produced, so the deck rendering is auditable and gaps are visible. State
is the shared `SessionState` enum: `idle`, `working`, `waiting`, `done`,
`error`, `unknown`. The deck color mapping is fixed: `working` is blue, `waiting`
is orange, rendered terminal `error` is red, `done` is green, and `idle` or
`unknown` are gray.

Include a table with one row per handled state:

| State | Deck color | Produced by |
| --- | --- | --- |
| `working` | blue | <the signals and inference paths that yield working> |
| `waiting` | orange | <the signals that yield waiting> |
| `done` | green | <the exact terminal signal(s) that yield done> |
| `error` | red | <the exact signals that yield a terminal error> |
| `idle` | gray | <session start/end and stale-signal fallbacks> |
| `unknown` | gray | <how stale non-terminal signals degrade, or `None established`> |

Then document, in prose:

- **Which state means "green".** Name the single authoritative path that
  produces `done`, and whether any inference path can reach it independently.
- **Terminal signal handling.** How terminal status values map to `done` or
  `error`, and what happens to a missing, empty, or unrecognized status. Prefer
  recording a terminal state at reduced confidence over dropping the signal and
  leaving a tile stuck.
- **Stale-signal expiry.** The terminal deadline after which a non-terminal
  (`working`/`waiting`) signal is expired when the expected terminal event never
  arrives, and whether the stale signal is removed (so inference resumes) or
  degraded to `unknown`.
- **Error surfacing.** How a session-level or model error becomes a visible
  `error` tile rather than a generic waiting or idle state.
- **Retained metadata.** Exactly which fields are retained transiently; confirm
  no prompt, response, or transcript content is stored.
