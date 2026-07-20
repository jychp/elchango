import Foundation

public enum JSONValue: Codable, Equatable, Sendable {
    case string(String)
    case integer(Int64)
    case double(Double)
    case boolean(Bool)
    case object([String: JSONValue])
    case array([JSONValue])
    case null

    public init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if container.decodeNil() {
            self = .null
        } else if let value = try? container.decode(Bool.self) {
            self = .boolean(value)
        } else if let value = try? container.decode(Int64.self) {
            self = .integer(value)
        } else if let value = try? container.decode(Double.self) {
            self = .double(value)
        } else if let value = try? container.decode(String.self) {
            self = .string(value)
        } else if let value = try? container.decode(
            [String: JSONValue].self
        ) {
            self = .object(value)
        } else if let value = try? container.decode([JSONValue].self) {
            self = .array(value)
        } else {
            throw DecodingError.dataCorruptedError(
                in: container,
                debugDescription: "unsupported JSON value"
            )
        }
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .string(let value):
            try container.encode(value)
        case .integer(let value):
            try container.encode(value)
        case .double(let value):
            try container.encode(value)
        case .boolean(let value):
            try container.encode(value)
        case .object(let value):
            try container.encode(value)
        case .array(let value):
            try container.encode(value)
        case .null:
            try container.encodeNil()
        }
    }
}

public struct ProviderActionResult: Codable, Equatable, Sendable {
    public let accepted: Bool
    public let verdict: String
    public let details: [String: JSONValue]

    public init(
        accepted: Bool,
        verdict: String,
        details: [String: JSONValue]
    ) {
        self.accepted = accepted
        self.verdict = verdict
        self.details = details
    }
}

public struct ProviderHookPayload: Codable, Equatable, Sendable {
    public let hookEventName: String?
    public let conversationID: String?
    public let generationID: String?
    public let composerMode: String?
    public let status: String?
    public let sessionID: String?
    public let cwd: String?
    public let transcriptPath: String?
    public let notificationType: String?
    public let toolName: String?
    public let promptID: String?
    public let turnID: String?
    public let permissionMode: String?
    public let compactTrigger: String?
    public let source: String?
    public let agentID: String?
    public let subagentID: String?
    public let backgroundTasks: [ProviderHookBackgroundTask]?

    public init(
        hookEventName: String? = nil,
        conversationID: String? = nil,
        generationID: String? = nil,
        composerMode: String? = nil,
        status: String? = nil,
        sessionID: String? = nil,
        cwd: String? = nil,
        transcriptPath: String? = nil,
        notificationType: String? = nil,
        toolName: String? = nil,
        promptID: String? = nil,
        turnID: String? = nil,
        permissionMode: String? = nil,
        compactTrigger: String? = nil,
        source: String? = nil,
        agentID: String? = nil,
        subagentID: String? = nil,
        backgroundTasks: [ProviderHookBackgroundTask]? = nil
    ) {
        self.hookEventName = hookEventName
        self.conversationID = conversationID
        self.generationID = generationID
        self.composerMode = composerMode
        self.status = status
        self.sessionID = sessionID
        self.cwd = cwd
        self.transcriptPath = transcriptPath
        self.notificationType = notificationType
        self.toolName = toolName
        self.promptID = promptID
        self.turnID = turnID
        self.permissionMode = permissionMode
        self.compactTrigger = compactTrigger
        self.source = source
        self.agentID = agentID
        self.subagentID = subagentID
        self.backgroundTasks = backgroundTasks
    }

    private enum CodingKeys: String, CodingKey {
        case hookEventName = "hook_event_name"
        case conversationID = "conversation_id"
        case generationID = "generation_id"
        case composerMode = "composer_mode"
        case status
        case sessionID = "session_id"
        case cwd
        case transcriptPath = "transcript_path"
        case notificationType = "notification_type"
        case toolName = "tool_name"
        case promptID = "prompt_id"
        case turnID = "turn_id"
        case permissionMode = "permission_mode"
        case compactTrigger = "trigger"
        case source
        case agentID = "agent_id"
        case subagentID = "subagent_id"
        case backgroundTasks = "background_tasks"
    }
}

public struct ProviderHookBackgroundTask: Codable, Equatable, Sendable {
    public let taskID: String?
    public let taskType: String?
    public let status: String?
    public let agentType: String?

    public init(
        taskID: String? = nil,
        taskType: String? = nil,
        status: String? = nil,
        agentType: String? = nil
    ) {
        self.taskID = taskID
        self.taskType = taskType
        self.status = status
        self.agentType = agentType
    }

    private enum CodingKeys: String, CodingKey {
        case taskID = "id"
        case taskType = "type"
        case status
        case agentType = "agent_type"
    }
}

public struct ActivityObservation: Codable, Equatable, Sendable {
    public let sessionID: String
    public let event: String
    public let observedAtMilliseconds: Int64
    public let state: SessionState
    public let confidence: DeckConfidence
    public let detail: String

    public init(
        sessionID: String,
        event: String,
        observedAtMilliseconds: Int64,
        state: SessionState,
        confidence: DeckConfidence,
        detail: String
    ) {
        self.sessionID = sessionID
        self.event = event
        self.observedAtMilliseconds = observedAtMilliseconds
        self.state = state
        self.confidence = confidence
        self.detail = detail
    }

    private enum CodingKeys: String, CodingKey {
        case sessionID = "session_id"
        case event
        case observedAtMilliseconds = "observed_at_ms"
        case state
        case confidence
        case detail
    }
}

public enum ProviderOperationError: LocalizedError, Equatable {
    case unsupported(String)
    case invalidHook(String)
    case targetUnverified(String)
    case system(String)

    public var errorDescription: String? {
        switch self {
        case .unsupported(let message),
            .invalidHook(let message),
            .targetUnverified(let message),
            .system(let message):
            message
        }
    }
}
