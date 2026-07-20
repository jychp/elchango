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
`SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PermissionRequest`,
`PostToolUse`, `PreCompact`, `PostCompact`, `SubagentStart`, `SubagentStop`,
`Stop`, and `SessionEnd`. Each handler invokes:

```text
/Applications/elChango.app/Contents/MacOS/elChangoHookReporter --provider codex
```

The reporter forwards only a minimal, sanitized metadata subset
(`hook_event_name`, `session_id`, `cwd`, `transcript_path`, `tool_name`,
`permission_mode`, `turn_id`) to `http://127.0.0.1:8765/api/hooks/codex`. It
never forwards prompt, assistant, tool input/output, or transcript content.

## Status

Live hook delivery has not yet been verified end to end against Codex Desktop,
and the install path for a local (non-marketplace) plugin is not yet confirmed.
See `docs/providers/codex.md` for the current conservative verdicts and open
questions.
