import Foundation

public final class CursorActivityStore: @unchecked Sendable {
    private struct TerminalState {
        let state: SessionState
        let detail: String
        let confidence: DeckConfidence
    }

    private struct Turn {
        var observation: ActivityObservation
        var generationID: String?
        var activeSubagentIDs: Set<String> = []
        var anonymousSubagentCount = 0
        var childFailed = false
        var deferredTerminal: TerminalState?

        var activeSubagentCount: Int {
            activeSubagentIDs.count + anonymousSubagentCount
        }
    }

    private let terminalDeadlineMilliseconds: Int64
    private let ttlMilliseconds: Int64
    private let maximumSignals: Int
    private let maximumActiveSubagents: Int
    private let lock = NSLock()
    private var turns: [String: Turn] = [:]
    private var acknowledgedAt: [String: Int64] = [:]

    public init(
        terminalDeadlineMilliseconds: Int64 = 10 * 60 * 1_000,
        ttlMilliseconds: Int64 = 60 * 60 * 1_000,
        maximumSignals: Int = 1_000,
        maximumActiveSubagents: Int = 64
    ) {
        self.terminalDeadlineMilliseconds = terminalDeadlineMilliseconds
        self.ttlMilliseconds = ttlMilliseconds
        self.maximumSignals = maximumSignals
        self.maximumActiveSubagents = maximumActiveSubagents
    }

    public func record(
        _ payload: ProviderHookPayload,
        observedAtMilliseconds: Int64
    ) throws -> ActivityObservation {
        let supported = Set([
            "sessionStart",
            "beforeSubmitPrompt",
            "preCompact",
            "subagentStart",
            "subagentStop",
            "afterAgentThought",
            "afterAgentResponse",
            "stop",
            "sessionEnd",
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
        if let subagentID = payload.subagentID,
            subagentID.isEmpty
        {
            throw ProviderOperationError.invalidHook(
                "Cursor hook subagent_id must be a non-empty string"
            )
        }
        if let mode = payload.composerMode,
            !Set(["agent", "plan", "ask", "edit"]).contains(mode)
        {
            throw ProviderOperationError.invalidHook(
                "unsupported Cursor composer mode: \(mode)"
            )
        }

        lock.lock()
        defer { lock.unlock() }
        purgeExpired(at: observedAtMilliseconds)
        evictOldestIfNeeded(for: sessionID)

        var turn =
            turns[sessionID]
            ?? Turn(
                observation: Self.observation(
                    sessionID: sessionID,
                    event: "sessionStart",
                    observedAtMilliseconds: observedAtMilliseconds,
                    state: .idle,
                    detail: "Cursor session started"
                ),
                generationID: nil
            )
        if event != "beforeSubmitPrompt",
            let currentGenerationID = turn.generationID,
            currentGenerationID != payload.generationID
        {
            return turn.observation
        }

        switch event {
        case "sessionStart":
            turn = Turn(
                observation: Self.observation(
                    sessionID: sessionID,
                    event: event,
                    observedAtMilliseconds: observedAtMilliseconds,
                    state: .idle,
                    detail: "Cursor session started"
                ),
                generationID: payload.generationID
            )
        case "beforeSubmitPrompt":
            if turn.generationID != payload.generationID {
                turn.activeSubagentIDs = []
                turn.anonymousSubagentCount = 0
                turn.childFailed = false
            }
            turn.generationID = payload.generationID
            turn.deferredTerminal = nil
            turn.observation = Self.observation(
                sessionID: sessionID,
                event: event,
                observedAtMilliseconds: observedAtMilliseconds,
                state: .working,
                detail: "Cursor prompt submitted"
            )
        case "preCompact", "afterAgentThought", "afterAgentResponse":
            if turn.deferredTerminal == nil,
                turn.observation.state == .done
                    || turn.observation.state == .error
            {
                return turn.observation
            }
            turn.deferredTerminal = nil
            turn.observation = Self.observation(
                sessionID: sessionID,
                event: event,
                observedAtMilliseconds: observedAtMilliseconds,
                state: .working,
                detail: Self.cursorProgressDetail(event)
            )
        case "subagentStart":
            if let subagentID = payload.subagentID,
                turn.activeSubagentIDs.contains(subagentID)
            {
                return turn.observation
            }
            guard turn.activeSubagentCount < maximumActiveSubagents else {
                throw ProviderOperationError.invalidHook(
                    "Cursor active subagent limit exceeded"
                )
            }
            if let subagentID = payload.subagentID {
                turn.activeSubagentIDs.insert(subagentID)
            } else {
                turn.anonymousSubagentCount += 1
            }
            turn.observation = Self.observation(
                sessionID: sessionID,
                event: event,
                observedAtMilliseconds: observedAtMilliseconds,
                state: .working,
                detail: "Cursor subagent working"
            )
        case "subagentStop":
            if let subagentID = payload.subagentID {
                guard turn.activeSubagentIDs.remove(subagentID) != nil else {
                    return turn.observation
                }
            } else {
                guard turn.anonymousSubagentCount > 0 else {
                    return turn.observation
                }
                turn.anonymousSubagentCount -= 1
            }
            if payload.status == "error" || payload.status == "aborted" {
                turn.childFailed = true
            }
            if turn.activeSubagentCount == 0,
                let deferred = turn.deferredTerminal
            {
                turn.deferredTerminal = nil
                turn.observation = Self.observation(
                    sessionID: sessionID,
                    event: event,
                    observedAtMilliseconds: observedAtMilliseconds,
                    state: turn.childFailed ? .error : deferred.state,
                    detail: turn.childFailed
                        ? "Cursor subagent failed or aborted"
                        : deferred.detail,
                    confidence: turn.childFailed ? .observed : deferred.confidence
                )
            } else {
                turn.observation = Self.observation(
                    sessionID: sessionID,
                    event: event,
                    observedAtMilliseconds: observedAtMilliseconds,
                    state: .working,
                    detail: "Cursor subagent activity continues"
                )
            }
        case "stop":
            let terminal = Self.cursorTerminalState(status: payload.status)
            if turn.activeSubagentCount > 0 {
                turn.deferredTerminal = terminal
                turn.observation = Self.observation(
                    sessionID: sessionID,
                    event: event,
                    observedAtMilliseconds: observedAtMilliseconds,
                    state: .working,
                    detail: "Cursor parent stopped; subagents still working"
                )
            } else {
                turn.observation = Self.observation(
                    sessionID: sessionID,
                    event: event,
                    observedAtMilliseconds: observedAtMilliseconds,
                    state: terminal.state,
                    detail: terminal.detail,
                    confidence: terminal.confidence
                )
            }
        case "sessionEnd":
            turn = Turn(
                observation: Self.observation(
                    sessionID: sessionID,
                    event: event,
                    observedAtMilliseconds: observedAtMilliseconds,
                    state: .idle,
                    detail: "Cursor session ended"
                ),
                generationID: payload.generationID
            )
        default:
            preconditionFailure("validated Cursor event was not handled")
        }
        turns[sessionID] = turn
        return turn.observation
    }

    public func state(
        for sessionID: String,
        observedAtMilliseconds: Int64,
        currentGenerationID: String? = nil
    ) -> (SessionState, DeckConfidence, String)? {
        lock.lock()
        defer { lock.unlock() }
        guard let turn = turns[sessionID] else { return nil }
        if let currentGenerationID,
            turn.generationID != currentGenerationID
        {
            return nil
        }
        if observedAtMilliseconds
            - turn.observation.observedAtMilliseconds
            > ttlMilliseconds
        {
            remove(sessionID)
            return nil
        }
        if turn.observation.state == .working
            || turn.observation.state == .waiting,
            observedAtMilliseconds
                - turn.observation.observedAtMilliseconds
                > terminalDeadlineMilliseconds
        {
            // Expire a non-terminal hook signal when the expected terminal
            // event never arrived, so database inference governs again
            // instead of the tile remaining stuck in working or waiting.
            remove(sessionID)
            return nil
        }
        if turn.observation.state == .done,
            let acknowledged = acknowledgedAt[sessionID],
            acknowledged >= turn.observation.observedAtMilliseconds
        {
            return (.idle, .observed, "completion acknowledged by focus")
        }
        return (
            turn.observation.state,
            turn.observation.confidence,
            turn.observation.detail
        )
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
        let expired = turns.compactMap { sessionID, turn in
            observedAtMilliseconds
                - turn.observation.observedAtMilliseconds
                > ttlMilliseconds
                ? sessionID
                : nil
        }
        for sessionID in expired {
            remove(sessionID)
        }
    }

    private func evictOldestIfNeeded(for sessionID: String) {
        guard turns[sessionID] == nil,
            turns.count >= maximumSignals,
            let oldest = turns.min(by: {
                $0.value.observation.observedAtMilliseconds
                    < $1.value.observation.observedAtMilliseconds
            })?.key
        else {
            return
        }
        remove(oldest)
    }

    private func remove(_ sessionID: String) {
        turns.removeValue(forKey: sessionID)
        acknowledgedAt.removeValue(forKey: sessionID)
    }

    private static func observation(
        sessionID: String,
        event: String,
        observedAtMilliseconds: Int64,
        state: SessionState,
        detail: String,
        confidence: DeckConfidence = .observed
    ) -> ActivityObservation {
        ActivityObservation(
            sessionID: sessionID,
            event: event,
            observedAtMilliseconds: observedAtMilliseconds,
            state: state,
            confidence: confidence,
            detail: detail
        )
    }

    private static func cursorProgressDetail(_ event: String) -> String {
        switch event {
        case "preCompact":
            "Cursor is compacting context"
        case "afterAgentThought":
            "Cursor planned its next move"
        case "afterAgentResponse":
            "Cursor produced a response"
        default:
            "Cursor agent working"
        }
    }

    private static func cursorTerminalState(
        status: String?
    ) -> TerminalState {
        switch status {
        case "completed":
            return TerminalState(
                state: .done,
                detail: "Cursor agent completed",
                confidence: .observed
            )
        case "error":
            return TerminalState(
                state: .error,
                detail: "Cursor agent stopped with error",
                confidence: .observed
            )
        case "aborted":
            return TerminalState(
                state: .error,
                detail: "Cursor agent aborted",
                confidence: .observed
            )
        default:
            // A stop event always means the turn ended, so record a terminal
            // state instead of dropping the signal. The exact outcome is
            // unknown, so report done with reduced confidence and preserve the
            // raw status for diagnosis rather than leaving the tile stuck in
            // working.
            return TerminalState(
                state: .done,
                detail: "Cursor agent stopped (status: \(status ?? "unspecified"))",
                confidence: .candidate
            )
        }
    }
}

public final class ClaudeActivityStore: @unchecked Sendable {
    private struct Turn {
        var observation: ActivityObservation
        var promptID: String?
        var activeAgentIDs: Set<String> = []
    }

    private let terminalDeadlineMilliseconds: Int64
    private let ttlMilliseconds: Int64
    private let maximumSignals: Int
    private let maximumActiveSubagents: Int
    private let lock = NSLock()
    private var turns: [String: Turn] = [:]
    private var acknowledgedAt: [String: Int64] = [:]

    public init(
        terminalDeadlineMilliseconds: Int64 = 10 * 60 * 1_000,
        ttlMilliseconds: Int64 = 60 * 60 * 1_000,
        maximumSignals: Int = 1_000,
        maximumActiveSubagents: Int = 64
    ) {
        self.terminalDeadlineMilliseconds = terminalDeadlineMilliseconds
        self.ttlMilliseconds = ttlMilliseconds
        self.maximumSignals = maximumSignals
        self.maximumActiveSubagents = maximumActiveSubagents
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
        if let promptID = payload.promptID, promptID.isEmpty {
            throw ProviderOperationError.invalidHook(
                "Claude Code prompt_id must be a non-empty string"
            )
        }
        if let permissionMode = payload.permissionMode,
            !Set([
                "default",
                "plan",
                "acceptEdits",
                "auto",
                "dontAsk",
                "bypassPermissions",
            ]).contains(permissionMode)
        {
            throw ProviderOperationError.invalidHook(
                "unsupported Claude Code permission_mode: \(permissionMode)"
            )
        }
        if let backgroundTasks = payload.backgroundTasks {
            guard backgroundTasks.count <= 64,
                backgroundTasks.allSatisfy({ task in
                    [
                        task.taskID,
                        task.taskType,
                        task.status,
                        task.agentType,
                    ].allSatisfy { value in
                        value.map {
                            (1...256).contains($0.utf8.count)
                        } ?? true
                    }
                })
            else {
                throw ProviderOperationError.invalidHook(
                    "Claude Code background task metadata exceeds limits"
                )
            }
        }
        let mapped = try Self.claudeState(
            event: event,
            notificationType: payload.notificationType,
            toolName: payload.toolName,
            compactTrigger: payload.compactTrigger,
            source: payload.source
        )
        lock.lock()
        defer { lock.unlock() }
        purgeExpired(at: observedAtMilliseconds)
        if turns[sessionID] == nil,
            turns.count >= maximumSignals,
            let oldest = turns.min(by: {
                $0.value.observation.observedAtMilliseconds
                    < $1.value.observation.observedAtMilliseconds
            })?.key
        {
            turns.removeValue(forKey: oldest)
            acknowledgedAt.removeValue(forKey: oldest)
        }
        var turn =
            turns[sessionID]
            ?? Turn(
                observation: ActivityObservation(
                    sessionID: sessionID,
                    event: "SessionStart",
                    observedAtMilliseconds: observedAtMilliseconds,
                    state: .idle,
                    confidence: .observed,
                    detail: "Claude Code session started"
                ),
                promptID: nil
            )
        if event == "UserPromptSubmit" {
            if turn.promptID != payload.promptID {
                turn.activeAgentIDs.removeAll()
            }
            turn.promptID = payload.promptID
        } else if let currentPromptID = turn.promptID,
            let eventPromptID = payload.promptID,
            currentPromptID != eventPromptID
        {
            return turn.observation
        }

        if event == "SubagentStart" {
            guard let agentID = payload.agentID, !agentID.isEmpty else {
                throw ProviderOperationError.invalidHook(
                    "Claude Code SubagentStart is missing agent_id"
                )
            }
            if turn.activeAgentIDs.contains(agentID) {
                return turn.observation
            }
            guard turn.activeAgentIDs.count < maximumActiveSubagents else {
                throw ProviderOperationError.invalidHook(
                    "Claude Code active subagent limit exceeded"
                )
            }
            turn.activeAgentIDs.insert(agentID)
        } else if event == "SubagentStop" {
            guard let agentID = payload.agentID, !agentID.isEmpty else {
                throw ProviderOperationError.invalidHook(
                    "Claude Code SubagentStop is missing agent_id"
                )
            }
            guard turn.activeAgentIDs.remove(agentID) != nil else {
                return turn.observation
            }
        }

        var state = mapped.state
        var detail = mapped.detail
        if event == "Stop" {
            let hasBackgroundTasks =
                payload.backgroundTasks?.isEmpty == false
                || (payload.backgroundTasks == nil
                    && !turn.activeAgentIDs.isEmpty)
            if hasBackgroundTasks {
                state = .working
                detail = "Claude Code background tasks still working"
            }
        } else if event != "UserPromptSubmit"
            && !(event == "SessionStart" && payload.source == "compact"),
            turn.observation.state == .done
                || turn.observation.state == .error,
            mapped.state == .working
        {
            return turn.observation
        }
        turn.observation = ActivityObservation(
            sessionID: sessionID,
            event: event,
            observedAtMilliseconds: observedAtMilliseconds,
            state: state,
            confidence: mapped.confidence,
            detail: detail
        )
        if event == "SessionEnd" {
            turn.activeAgentIDs.removeAll()
            turn.promptID = nil
        }
        turns[sessionID] = turn
        return turn.observation
    }

    public func state(
        for sessionID: String,
        observedAtMilliseconds: Int64
    ) -> (SessionState, DeckConfidence, String)? {
        lock.lock()
        defer { lock.unlock() }
        guard let turn = turns[sessionID] else { return nil }
        let signal = turn.observation
        let age = observedAtMilliseconds - signal.observedAtMilliseconds
        if age > ttlMilliseconds {
            turns.removeValue(forKey: sessionID)
            acknowledgedAt.removeValue(forKey: sessionID)
            return nil
        }
        if signal.state == .working || signal.state == .waiting,
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
        let expired = turns.compactMap { sessionID, turn in
            observedAtMilliseconds - turn.observation.observedAtMilliseconds
                > ttlMilliseconds
                ? sessionID
                : nil
        }
        for sessionID in expired {
            turns.removeValue(forKey: sessionID)
            acknowledgedAt.removeValue(forKey: sessionID)
        }
    }

    private static func claudeState(
        event: String,
        notificationType: String?,
        toolName: String?,
        compactTrigger: String?,
        source: String?
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
            return source == "compact"
                ? (
                    .working,
                    .observed,
                    "Claude Code resumed after compaction"
                )
                : (.idle, .observed, "Claude Code session started")
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
        case "PostToolBatch":
            return (
                .working,
                .observed,
                "Claude Code completed a tool batch"
            )
        case "PermissionDenied":
            guard let toolName, !toolName.isEmpty else {
                throw ProviderOperationError.invalidHook(
                    "Claude Code PermissionDenied is missing tool_name"
                )
            }
            return (
                .working,
                .observed,
                "Claude Code permission was denied; agent may retry"
            )
        case "SubagentStart":
            return (.working, .observed, "Claude Code subagent started")
        case "SubagentStop":
            return (.working, .observed, "Claude Code subagent stopped")
        case "PreCompact", "PostCompact":
            guard let compactTrigger,
                compactTrigger == "manual" || compactTrigger == "auto"
            else {
                throw ProviderOperationError.invalidHook(
                    "Claude Code \(event) has an unsupported trigger"
                )
            }
            return (
                .working,
                .observed,
                event == "PreCompact"
                    ? "Claude Code is compacting context"
                    : "Claude Code context compaction completed"
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
            if notificationType == "idle_prompt" {
                return (
                    .done,
                    .observed,
                    "Claude Code turn completed: \(notificationType)"
                )
            }
            if notificationType == "agent_completed" {
                return (
                    .working,
                    .candidate,
                    "Claude Code agent completion requires reconciliation"
                )
            }
            if notificationType == "elicitation_complete"
                || notificationType == "elicitation_response"
            {
                return (
                    .working,
                    .observed,
                    "Claude Code input received: \(notificationType)"
                )
            }
            throw ProviderOperationError.invalidHook(
                "unsupported Claude Code notification: \(notificationType)"
            )
        default:
            throw ProviderOperationError.invalidHook(
                "unsupported Claude Code hook event: \(event)"
            )
        }
    }
}

/// Activity store for Codex Desktop plugin hooks. Codex uses the same hook file
/// schema and payload field names as Claude Code (official docs:
/// https://learn.chatgpt.com/docs/hooks), but only `type: "command"` hooks run,
/// so elChango relays events through `elChangoHookReporter --provider codex`.
public final class CodexActivityStore: @unchecked Sendable {
    private struct Turn {
        var observation: ActivityObservation
        var activeAgentCount: Int = 0
    }

    private let terminalDeadlineMilliseconds: Int64
    private let ttlMilliseconds: Int64
    private let maximumSignals: Int
    private let maximumActiveSubagents: Int
    private let lock = NSLock()
    private var turns: [String: Turn] = [:]
    private var acknowledgedAt: [String: Int64] = [:]

    public init(
        terminalDeadlineMilliseconds: Int64 = 10 * 60 * 1_000,
        ttlMilliseconds: Int64 = 60 * 60 * 1_000,
        maximumSignals: Int = 1_000,
        maximumActiveSubagents: Int = 64
    ) {
        self.terminalDeadlineMilliseconds = terminalDeadlineMilliseconds
        self.ttlMilliseconds = ttlMilliseconds
        self.maximumSignals = maximumSignals
        self.maximumActiveSubagents = maximumActiveSubagents
    }

    public func record(
        _ payload: ProviderHookPayload,
        observedAtMilliseconds: Int64
    ) throws -> ActivityObservation {
        guard let event = payload.hookEventName else {
            throw ProviderOperationError.invalidHook(
                "unsupported Codex hook event: nil"
            )
        }
        guard let sessionID = payload.sessionID, !sessionID.isEmpty else {
            throw ProviderOperationError.invalidHook(
                "Codex hook event is missing session_id"
            )
        }
        let mapped = try Self.codexState(
            event: event,
            toolName: payload.toolName
        )

        lock.lock()
        defer { lock.unlock() }
        purgeExpired(at: observedAtMilliseconds)
        if turns[sessionID] == nil,
            turns.count >= maximumSignals,
            let oldest = turns.min(by: {
                $0.value.observation.observedAtMilliseconds
                    < $1.value.observation.observedAtMilliseconds
            })?.key
        {
            turns.removeValue(forKey: oldest)
            acknowledgedAt.removeValue(forKey: oldest)
        }
        var turn =
            turns[sessionID]
            ?? Turn(
                observation: ActivityObservation(
                    sessionID: sessionID,
                    event: "SessionStart",
                    observedAtMilliseconds: observedAtMilliseconds,
                    state: .idle,
                    confidence: .observed,
                    detail: "Codex session started"
                )
            )

        if event == "SubagentStart" {
            guard turn.activeAgentCount < maximumActiveSubagents else {
                throw ProviderOperationError.invalidHook(
                    "Codex active subagent limit exceeded"
                )
            }
            turn.activeAgentCount += 1
        } else if event == "SubagentStop" {
            turn.activeAgentCount = max(0, turn.activeAgentCount - 1)
        }

        var state = mapped.state
        var detail = mapped.detail
        if event == "Stop", turn.activeAgentCount > 0 {
            state = .working
            detail = "Codex subagents still working"
        }

        turn.observation = ActivityObservation(
            sessionID: sessionID,
            event: event,
            observedAtMilliseconds: observedAtMilliseconds,
            state: state,
            confidence: mapped.confidence,
            detail: detail
        )
        if event == "SessionEnd" {
            turn.activeAgentCount = 0
        }
        turns[sessionID] = turn
        return turn.observation
    }

    public func state(
        for sessionID: String,
        observedAtMilliseconds: Int64
    ) -> (SessionState, DeckConfidence, String)? {
        lock.lock()
        defer { lock.unlock() }
        guard let turn = turns[sessionID] else { return nil }
        let signal = turn.observation
        let age = observedAtMilliseconds - signal.observedAtMilliseconds
        if age > ttlMilliseconds {
            turns.removeValue(forKey: sessionID)
            acknowledgedAt.removeValue(forKey: sessionID)
            return nil
        }
        if signal.state == .working || signal.state == .waiting,
            age > terminalDeadlineMilliseconds
        {
            return (
                .unknown,
                .unknown,
                "Codex \(signal.event) signal is stale; expected terminal event was not observed"
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
        let expired = turns.compactMap { sessionID, turn in
            observedAtMilliseconds - turn.observation.observedAtMilliseconds
                > ttlMilliseconds
                ? sessionID
                : nil
        }
        for sessionID in expired {
            turns.removeValue(forKey: sessionID)
            acknowledgedAt.removeValue(forKey: sessionID)
        }
    }

    private static func codexState(
        event: String,
        toolName: String?
    ) throws -> (state: SessionState, confidence: DeckConfidence, detail: String) {
        switch event {
        case "SessionStart":
            return (.idle, .observed, "Codex session started")
        case "SessionEnd":
            return (.idle, .observed, "Codex session ended")
        case "UserPromptSubmit":
            return (.working, .observed, "Codex prompt submitted")
        case "PreToolUse":
            return (.working, .observed, "Codex is running a tool")
        case "PostToolUse":
            return (.working, .observed, "Codex tool finished; turn continues")
        case "PermissionRequest":
            guard let toolName, !toolName.isEmpty else {
                throw ProviderOperationError.invalidHook(
                    "Codex PermissionRequest is missing tool_name"
                )
            }
            return (.waiting, .observed, "Codex needs permission: \(toolName)")
        case "PreCompact":
            return (.working, .observed, "Codex is compacting context")
        case "PostCompact":
            return (.working, .observed, "Codex context compaction completed")
        case "SubagentStart":
            return (.working, .observed, "Codex subagent started")
        case "SubagentStop":
            return (.working, .observed, "Codex subagent stopped")
        case "Stop":
            return (.done, .observed, "Codex turn completed")
        default:
            throw ProviderOperationError.invalidHook(
                "unsupported Codex hook event: \(event)"
            )
        }
    }
}
