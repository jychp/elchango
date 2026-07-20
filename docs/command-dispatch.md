# Native text command dispatch contract

elChango uses one shared transaction for text commands sent to Cursor and
Claude Desktop. Provider adapters own their semantic recipes and submission
counts, but they do not own text replacement or draft restoration.

## Target model

A command acts on whatever session the frontmost harness window currently has on
screen. elChango verifies only that a command-capable provider is the frontmost
application; it does not verify which provider-native session is selected, because
the desktop harnesses expose no reliable live signal for it. The user is
responsible for having the intended session in front. (An earlier design required
the exact selected session to match; that check was removed because it made
commands unusable after a manual window focus.)

## Preconditions

Command dispatch performs these steps in order:

1. Activate or open the provider application.
2. Verify that the expected bundle is frontmost and retain its process ID.
3. Verify the focused, enabled Accessibility text input and its exact
   provider-specific marker. If the expected input is not already focused, the
   provider-approved focus shortcut may run once.
The verified transaction does not mutate input before these preconditions pass.

Claude has one explicit best-effort exception when Accessibility successfully
reports the observed non-text `AXGroup` role. The application and process remain
mandatory, but elChango skips input verification, draft capture, deletion, and
restoration. It sends the command and provider-owned submission keys through
Claude's observed application-level input routing. Missing Accessibility
evidence, another non-text role, a wrong text-input marker, or a disabled input
rejects the action without typing. This path never reports verified input
mutation or draft preservation.

Codex uses a separate, naive best-effort path (Electron/Chromium exposes no
verified input marker): `accept` posts a double `Cmd+Return` to the frontmost
window, and the text commands type a fixed prompt into whatever input has focus
and submit. Codex dispatch does no input-marker verification, draft capture, or
restoration, and is confirmed by the user.

## Transaction

This verified transaction applies to Cursor and Claude. After preflight,
elChango:

1. rechecks that the harness is frontmost;
2. selects all input with a globally posted, held `Cmd+A`;
3. captures the complete Accessibility selected text as a bounded in-memory
   draft;
4. types the bounded command directly over the selection through the
   provider-proven Unicode keyboard transport;
5. verifies that the complete input value exactly matches the command;
6. rechecks the frontmost process and focused input before each provider-owned
   submission key;
7. waits for a newly acquired, verified input to become empty, which is the
   observable submission boundary;
8. types the original draft into that empty input; and
9. verifies that the complete input value exactly matches the original draft.

Electron may expose an otherwise exact input value with one trailing newline.
Command, empty-input, and restored-draft comparisons normalize only that one
provider artifact before comparing values.

The command is not reported as successfully dispatched until the observable
submission boundary passes and any nonempty original draft is restored.

## Clipboard prohibition

Command dispatch must not read, write, or temporarily replace the macOS
clipboard. Draft capture uses Accessibility and remains in bounded process
memory. Command insertion and draft restoration use keyboard events.

Electron editors have previously ignored PID-targeted Unicode events.
Therefore, text and focused-key events use the global HID tap. The expected
bundle and process ID are checked immediately before each event or text chunk;
verified-path text chunks additionally recheck the exact input. The frontmost
harness is checked immediately before replacement and each submission key, but
not between every HID event. elChango does not track which session is on screen,
so a session switch during dispatch is not detected. Application shortcuts target
the verified process ID
except for the intentional global-HID `Cmd+A` used by the proven Electron
selection path.

## Failure and compensation

Every failed verified-path precondition stops before input mutation. A failure
before any submission attempts to restore the captured draft when the harness is
still frontmost and the focused input can still be reacquired. Because this
compensation
does not require the current value to still equal the command, a concurrent
editor change can be overwritten. After a submission attempt, compensation
requires the current input to still match the command.

Submission changes another application's state and cannot be made truly
atomic. If the command was submitted but draft restoration cannot be verified,
the action fails explicitly rather than claiming success. elChango does not
retry a semantic command automatically because a retry could duplicate it.

## Provider-owned differences

Cursor and Claude Desktop may use different focus shortcuts, command text,
slash commands, and submission counts. Those differences cannot weaken the
shared activation, frontmost verification, replacement, submission observation,
or draft restoration contract except for Claude's explicitly documented
best-effort path.
