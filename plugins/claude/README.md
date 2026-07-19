# elChango for Claude Code

This plugin reports supported Claude Code lifecycle events to the native
elChango app so its web and Stream Deck surfaces can display live session
state.

## Requirements

- macOS
- Claude Code, including sessions opened by Claude Desktop
- The native elChango app running locally on `127.0.0.1:8765`

## Hooks

The plugin uses Claude Code's native HTTP hooks to post directly to:

```text
http://127.0.0.1:8765/api/hooks/claude-code
```

It reports session start, prompt submission, supported waiting transitions,
normal and failed stops, and session end. The `PreToolUse` and `PostToolUse`
handlers are limited to `AskUserQuestion` and `ExitPlanMode`. Notification
handlers are limited to `permission_prompt`, `idle_prompt`,
`elicitation_dialog`, and `agent_needs_input`.

Each request times out after two seconds. Claude Code treats connection failures
and HTTP hook timeouts as non-blocking errors, so an unavailable elChango
service does not stop the agent.

The native provider retains no prompt, assistant, notification message, or
transcript content. Hook evidence affects a session only when `session_id`
exactly matches a current persistent Claude Code session.

Claude's HTTP-hook format does not distribute elChango's per-install control
token. Hook routes therefore cannot execute actions and are isolated from the
authenticated control API. They reject browser origins, validate sanitized
lifecycle fields against current inventory, and are rate-limited. See the root
[security policy](../../SECURITY.md) for the residual spoofing limitation.

## Installation

Add the repository marketplace and install the plugin:

```bash
claude plugin marketplace add jychp/elchango
claude plugin install elchango@elchango
```

Start the elChango app before beginning or resuming a Claude Code session.
