# elChango for Codex

This plugin reports Codex Desktop lifecycle events to the native elChango app so
its web and Stream Deck surfaces can display live session state.

## Requirements

- macOS
- Codex Desktop (the Codex app in ChatGPT, bundle `com.openai.codex`)
- elChango installed at `/Applications/elChango.app`
- The elChango app running locally

## Hooks

Codex uses the same hook file schema as Claude Code, but only `type: "command"`
handlers run, so this plugin relays each event through the elChango reporter
command instead of an HTTP handler. The plugin registers fail-open handlers for
the ten Codex hook events (per the official docs,
https://learn.chatgpt.com/docs/hooks): `SessionStart`, `UserPromptSubmit`,
`PreToolUse`, `PermissionRequest`, `PostToolUse`, `PreCompact`, `PostCompact`,
`SubagentStart`, `SubagentStop`, and `Stop`. Each handler invokes:

```text
/Applications/elChango.app/Contents/MacOS/elChangoHookReporter --provider codex
```

The reporter forwards only a minimal, sanitized metadata subset
(`hook_event_name`, `session_id`, `cwd`, `transcript_path`, `tool_name`,
`permission_mode`, `turn_id`) to `http://127.0.0.1:8765/api/hooks/codex`. It
never forwards prompt, assistant, tool input/output, or transcript content.

## Install

elChango installs and updates this plugin through Codex's official commands:

```text
codex plugin marketplace add jychp/elchango
codex plugin marketplace upgrade elchango
codex plugin add elchango@elchango
```

The menu bar app offers **Install/Update Codex Plugin** when it detects the
plugin is missing or out of date.

### Trust the hooks

Codex does not run a plugin's command hooks until they are trusted. The first
time a Codex session loads this plugin, Codex prompts to review and trust its
command hooks: approve them so the reporter may run. Trust is persisted per hook
in `~/.codex/config.toml` under `[hooks.state]` as a `trusted_hash`, and a
changed hook command re-prompts. There is no dedicated `codex plugin trust`
command; `codex --dangerously-bypass-hook-trust` skips the gate for a single
invocation but is dangerous and is not for normal use. Until the hooks are
trusted, sessions stay idle on the deck (no live state).

## Status

Live hook delivery has not yet been verified end to end against Codex Desktop.
See `docs/providers/codex.md` for the current conservative verdicts and open
questions.
