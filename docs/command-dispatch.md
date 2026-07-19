# Native text command dispatch contract

elChango uses one shared transaction for text commands sent to Cursor and
Claude Desktop. Provider adapters own their semantic recipes and submission
counts, but they do not own text replacement or draft restoration.

## Preconditions

The action must resolve to exactly one provider-native session before native
automation starts. Command dispatch then performs these steps in order:

1. Activate or open the provider application.
2. Verify that the expected bundle is frontmost and retain its process ID.
3. Verify that the same provider-native session is still selected.
4. Verify the focused, enabled Accessibility text input and its exact
   provider-specific marker. If the expected input is not already focused, the
   provider-approved focus shortcut may run once before verification is
   repeated.
No input mutation is allowed before all four preconditions pass.

## Transaction

After preflight, elChango:

1. rechecks the selected native session;
2. selects all input with a globally posted, held `Cmd+A`;
3. captures the complete Accessibility selected text as a bounded in-memory
   draft;
4. types the bounded command directly over the selection through the
   provider-proven Unicode keyboard transport;
5. verifies that the complete input value exactly matches the command;
6. rechecks the frontmost process, focused input, and selected session before
   each provider-owned submission key;
7. waits for a newly acquired, verified input to become empty, which is the
   observable submission boundary;
8. types the original draft into that empty input; and
9. verifies that the complete input value exactly matches the original draft.

The command is not reported as successfully dispatched until the observable
submission boundary passes and any nonempty original draft is restored.

## Clipboard prohibition

Command dispatch must not read, write, or temporarily replace the macOS
clipboard. Draft capture uses Accessibility and remains in bounded process
memory. Command insertion and draft restoration use keyboard events.

Electron editors have previously ignored PID-targeted Unicode events.
Therefore, text and focused-key events use the global HID tap only while the
expected bundle, process ID, selected native session, and exact input remain
verified. Application shortcuts continue to target the verified process ID.

## Failure and compensation

Every failed precondition stops before input mutation. A failure after command
replacement but before an observed submission attempts to restore the captured
draft when the exact target and command text are still verified.

Submission changes another application's state and cannot be made truly
atomic. If the command was submitted but draft restoration cannot be verified,
the action fails explicitly rather than claiming success. elChango does not
retry a semantic command automatically because a retry could duplicate it.

## Provider-owned differences

Cursor and Claude Desktop may use different focus shortcuts, command text,
slash commands, and submission counts. Those differences cannot weaken the
shared activation, target verification, replacement, submission observation,
or draft restoration contract.
