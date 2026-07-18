import Foundation

public final class CursorActivityStore: @unchecked Sendable {
    private struct Signal {
        let observation: ActivityObservation
        let generationID: String?
    }

    private let ttlMilliseconds: Int64
    private let maximumSignals: Int
    private let lock = NSLock()
    private var signals: [String: Signal] = [:]
    private var composerModes: [String: String] = [:]
    private var acknowledgedAt: [String: Int64] = [:]
    private var selectedSessionID: String?
    private var selectionInitialized = false

    public init(
        ttlMilliseconds: Int64 = 60 * 60 * 1_000,
        maximumSignals: Int = 1_000
    ) {
        self.ttlMilliseconds = ttlMilliseconds
        self.maximumSignals = maximumSignals
    }

    public func record(
        _ payload: ProviderHookPayload,
        observedAtMilliseconds: Int64
    ) throws -> ActivityObservation {
        let supported = Set([
            "sessionStart", "beforeSubmitPrompt", "stop", "sessionEnd",
        ])
        guard let event = payload.hookEventName,
            supported.contains(event)
        else {
            throw ProviderOperationError.invalidHook(
                "unsupported Cursor hook event: \(payload.hookEventName ?? "nil")"
            )
        }
        guard let sessionID = payload.conversationID,
            !sessionID.isEmpty
        else {
            throw ProviderOperationError.invalidHook(
                "Cursor hook event is missing conversation_id"
            )
        }
        if let generationID = payload.generationID,
            generationID.isEmpty
        {
            throw ProviderOperationError.invalidHook(
                "Cursor hook generation_id must be a non-empty string"
            )
        }
        if let mode = payload.composerMode,
            mode != "agent" && mode != "plan"
        {
            throw ProviderOperationError.invalidHook(
                "unsupported Cursor composer mode: \(mode)"
            )
        }

        lock.lock()
        defer { lock.unlock() }
        purgeExpired(at: observedAtMilliseconds)
        evictOldestIfNeeded(for: sessionID)
        if event == "beforeSubmitPrompt" {
            composerModes[sessionID] = payload.composerMode
        }
        let mapped = try Self.cursorState(
            event: event,
            status: payload.status,
            composerMode: composerModes[sessionID]
        )
        let observation = ActivityObservation(
            sessionID: sessionID,
            event: event,
            observedAtMilliseconds: observedAtMilliseconds,
            state: mapped.state,
            confidence: mapped.confidence,
            detail: mapped.detail
        )
        signals[sessionID] = Signal(
            observation: observation,
            generationID: payload.generationID
        )
        if event == "sessionEnd" {
            composerModes.removeValue(forKey: sessionID)
        }
        return observation
    }

    public func state(
        for sessionID: String,
        observedAtMilliseconds: Int64,
        currentGenerationID: String? = nil
    ) -> (SessionState, DeckConfidence, String)? {
        lock.lock()
        defer { lock.unlock() }
        guard let signal = signals[sessionID] else { return nil }
        if observedAtMilliseconds
            - signal.observation.observedAtMilliseconds
            > ttlMilliseconds
        {
            remove(sessionID)
            return nil
        }
        if (signal.observation.state == .done
            || signal.observation.state == .waiting),
            let signalGenerationID = signal.generationID,
            let currentGenerationID,
            signalGenerationID != currentGenerationID
        {
            return nil
        }
        if signal.observation.state == .done,
            let acknowledged = acknowledgedAt[sessionID],
            acknowledged >= signal.observation.observedAtMilliseconds
        {
            return (.idle, .observed, "completion acknowledged by focus")
        }
        return (
            signal.observation.state,
            signal.observation.confidence,
            signal.observation.detail
        )
    }

    public func observeSelection(
        _ sessionID: String?,
        observedAtMilliseconds: Int64
    ) {
        lock.lock()
        defer { lock.unlock() }
        guard selectionInitialized else {
            selectedSessionID = sessionID
            selectionInitialized = true
            return
        }
        guard sessionID != selectedSessionID else { return }
        selectedSessionID = sessionID
        if let sessionID {
            acknowledgedAt[sessionID] = observedAtMilliseconds
        }
    }

    public func acknowledge(
        _ sessionID: String,
        observedAtMilliseconds: Int64
    ) {
        lock.lock()
        acknowledgedAt[sessionID] = observedAtMilliseconds
        lock.unlock()
    }

    private func purgeExpired(at observedAtMilliseconds: Int64) {
        let expired = signals.compactMap { sessionID, signal in
            observedAtMilliseconds
                - signal.observation.observedAtMilliseconds
                > ttlMilliseconds
                ? sessionID
                : nil
        }
        for sessionID in expired {
            remove(sessionID)
        }
    }

    private func evictOldestIfNeeded(for sessionID: String) {
        guard signals[sessionID] == nil,
            signals.count >= maximumSignals,
            let oldest = signals.min(by: {
                $0.value.observation.observedAtMilliseconds
                    < $1.value.observation.observedAtMilliseconds
            })?.key
        else {
            return
        }
        remove(oldest)
    }

    private func remove(_ sessionID: String) {
        signals.removeValue(forKey: sessionID)
        composerModes.removeValue(forKey: sessionID)
        acknowledgedAt.removeValue(forKey: sessionID)
    }

    private static func cursorState(
        event: String,
        status: String?,
        composerMode: String?
    ) throws -> (state: SessionState, confidence: DeckConfidence, detail: String) {
        switch event {
        case "beforeSubmitPrompt":
            return (.working, .observed, "Cursor prompt submitted")
        case "stop":
            switch status {
            case "error":
                return (
                    .waiting,
                    .observed,
                    "Cursor agent stopped with error"
                )
            case "completed":
                if composerMode == "plan" {
                    return (
                        .waiting,
                        .observed,
                        "Cursor plan awaiting approval"
                    )
                }
                return (.done, .observed, "Cursor agent completed")
            case "aborted":
                return (.idle, .observed, "Cursor agent aborted")
            default:
                throw ProviderOperationError.invalidHook(
                    "unsupported Cursor stop status: \(status ?? "nil")"
                )
            }
        case "sessionEnd":
            return (.idle, .observed, "Cursor session ended")
        default:
            return (.idle, .observed, "Cursor session started")
        }
    }
}

public final class ClaudeActivityStore: @unchecked Sendable {
    private let terminalDeadlineMilliseconds: Int64
    private let ttlMilliseconds: Int64
    private let maximumSignals: Int
    private let lock = NSLock()
    private var signals: [String: ActivityObservation] = [:]
    private var acknowledgedAt: [String: Int64] = [:]

    public init(
        terminalDeadlineMilliseconds: Int64 = 10 * 60 * 1_000,
        ttlMilliseconds: Int64 = 60 * 60 * 1_000,
        maximumSignals: Int = 1_000
    ) {
        self.terminalDeadlineMilliseconds = terminalDeadlineMilliseconds
        self.ttlMilliseconds = ttlMilliseconds
        self.maximumSignals = maximumSignals
    }

    public func record(
        _ payload: ProviderHookPayload,
        observedAtMilliseconds: Int64
    ) throws -> ActivityObservation {
        guard let event = payload.hookEventName else {
            throw ProviderOperationError.invalidHook(
                "unsupported Claude Code hook event: nil"
            )
        }
        guard let sessionID = payload.sessionID, !sessionID.isEmpty else {
            throw ProviderOperationError.invalidHook(
                "Claude Code hook event is missing session_id"
            )
        }
        guard let cwd = payload.cwd, !cwd.isEmpty else {
            throw ProviderOperationError.invalidHook(
                "Claude Code hook event is missing cwd"
            )
        }
        guard let transcriptPath = payload.transcriptPath,
            !transcriptPath.isEmpty
        else {
            throw ProviderOperationError.invalidHook(
                "Claude Code hook event is missing transcript_path"
            )
        }
        let mapped = try Self.claudeState(
            event: event,
            notificationType: payload.notificationType,
            toolName: payload.toolName
        )
        let observation = ActivityObservation(
            sessionID: sessionID,
            event: event,
            observedAtMilliseconds: observedAtMilliseconds,
            state: mapped.state,
            confidence: mapped.confidence,
            detail: mapped.detail
        )
        lock.lock()
        defer { lock.unlock() }
        purgeExpired(at: observedAtMilliseconds)
        if signals[sessionID] == nil,
            signals.count >= maximumSignals,
            let oldest = signals.min(by: {
                $0.value.observedAtMilliseconds
                    < $1.value.observedAtMilliseconds
            })?.key
        {
            signals.removeValue(forKey: oldest)
            acknowledgedAt.removeValue(forKey: oldest)
        }
        signals[sessionID] = observation
        return observation
    }

    public func state(
        for sessionID: String,
        observedAtMilliseconds: Int64
    ) -> (SessionState, DeckConfidence, String)? {
        lock.lock()
        defer { lock.unlock() }
        guard let signal = signals[sessionID] else { return nil }
        let age = observedAtMilliseconds - signal.observedAtMilliseconds
        if age > ttlMilliseconds {
            signals.removeValue(forKey: sessionID)
            acknowledgedAt.removeValue(forKey: sessionID)
            return nil
        }
        if (signal.state == .working || signal.state == .waiting),
            age > terminalDeadlineMilliseconds
        {
            return (
                .unknown,
                .unknown,
                "Claude Code \(signal.event) signal is stale; expected terminal event was not observed"
            )
        }
        if signal.state == .done,
            let acknowledged = acknowledgedAt[sessionID],
            acknowledged >= signal.observedAtMilliseconds
        {
            return (.idle, .observed, "completion acknowledged by focus")
        }
        return (signal.state, signal.confidence, signal.detail)
    }

    public func acknowledge(
        _ sessionID: String,
        observedAtMilliseconds: Int64
    ) {
        lock.lock()
        acknowledgedAt[sessionID] = observedAtMilliseconds
        lock.unlock()
    }

    private func purgeExpired(at observedAtMilliseconds: Int64) {
        let expired = signals.compactMap { sessionID, signal in
            observedAtMilliseconds - signal.observedAtMilliseconds
                > ttlMilliseconds
                ? sessionID
                : nil
        }
        for sessionID in expired {
            signals.removeValue(forKey: sessionID)
            acknowledgedAt.removeValue(forKey: sessionID)
        }
    }

    private static func claudeState(
        event: String,
        notificationType: String?,
        toolName: String?
    ) throws -> (state: SessionState, confidence: DeckConfidence, detail: String) {
        switch event {
        case "UserPromptSubmit":
            return (.working, .observed, "Claude Code prompt submitted")
        case "Stop":
            return (.done, .observed, "Claude Code turn completed")
        case "StopFailure":
            return (.error, .observed, "Claude Code turn failed")
        case "SessionEnd":
            return (.idle, .observed, "Claude Code session ended")
        case "SessionStart":
            return (.idle, .observed, "Claude Code session started")
        case "PermissionRequest":
            guard let toolName, !toolName.isEmpty else {
                throw ProviderOperationError.invalidHook(
                    "Claude Code PermissionRequest is missing tool_name"
                )
            }
            return (
                .waiting,
                .observed,
                "Claude Code needs permission: \(toolName)"
            )
        case "Elicitation":
            return (
                .waiting,
                .observed,
                "Claude Code MCP tool needs input"
            )
        case "ElicitationResult":
            return (
                .working,
                .observed,
                "Claude Code MCP input received"
            )
        case "PreToolUse", "PostToolUse":
            let waitingTools = Set(["AskUserQuestion", "ExitPlanMode"])
            guard let toolName, waitingTools.contains(toolName) else {
                throw ProviderOperationError.invalidHook(
                    "unsupported Claude Code \(event == "PreToolUse" ? "waiting" : "resumed") tool: \(toolName ?? "nil")"
                )
            }
            return event == "PreToolUse"
                ? (
                    .waiting,
                    .observed,
                    "Claude Code needs input: \(toolName)"
                )
                : (
                    .working,
                    .observed,
                    "Claude Code input received: \(toolName)"
                )
        case "Notification":
            let waitingNotifications = Set([
                "permission_prompt",
                "idle_prompt",
                "elicitation_dialog",
                "agent_needs_input",
            ])
            guard let notificationType, !notificationType.isEmpty else {
                throw ProviderOperationError.invalidHook(
                    "Claude Code Notification is missing notification_type"
                )
            }
            if waitingNotifications.contains(notificationType) {
                return (
                    .waiting,
                    .observed,
                    "Claude Code is waiting: \(notificationType)"
                )
            }
            return (
                .idle,
                .candidate,
                "Claude Code notification does not prove a deck state: \(notificationType)"
            )
        default:
            throw ProviderOperationError.invalidHook(
                "unsupported Claude Code hook event: \(event)"
            )
        }
    }
}
