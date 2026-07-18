import Foundation

public enum SessionState: String, Codable, Sendable {
    case idle
    case working
    case waiting
    case done
    case error
    case unknown
}

public enum ProviderCapability: String, Codable, Hashable, Sendable {
    case focusSession = "focus_session"
    case newSession = "new_session"
    case executeCommand = "execute_command"
}

public struct AgentSession: Equatable, Sendable {
    public let providerID: String
    public let nativeID: String
    public let capabilities: Set<ProviderCapability>
    public let icon: DeckIcon
    public let title: String
    public let workspaceID: String
    public let workspacePath: String?
    public let state: SessionState
    public let confidence: DeckConfidence
    public let stateDetail: String
    public let selected: Bool
    public let lastActivityAtMilliseconds: Int64
    public let commands: Set<CommandID>

    public init(
        providerID: String,
        nativeID: String,
        capabilities: Set<ProviderCapability>,
        icon: DeckIcon,
        title: String,
        workspaceID: String,
        workspacePath: String?,
        state: SessionState,
        confidence: DeckConfidence,
        stateDetail: String,
        selected: Bool,
        lastActivityAtMilliseconds: Int64,
        commands: Set<CommandID> = []
    ) {
        self.providerID = providerID
        self.nativeID = nativeID
        self.capabilities = capabilities
        self.icon = icon
        self.title = title
        self.workspaceID = workspaceID
        self.workspacePath = workspacePath
        self.state = state
        self.confidence = confidence
        self.stateDetail = stateDetail
        self.selected = selected
        self.lastActivityAtMilliseconds = lastActivityAtMilliseconds
        self.commands = commands
    }

    public var id: String {
        "\(providerID):\(nativeID)"
    }
}

public struct ProviderSnapshot: Equatable, Sendable {
    public let providerID: String
    public let capabilities: Set<ProviderCapability>
    public let observedAtMilliseconds: Int64
    public let selectedNativeSessionID: String?
    public let sessions: [AgentSession]
    public let source: String
    public let readOnly: Bool

    public init(
        providerID: String,
        capabilities: Set<ProviderCapability>,
        observedAtMilliseconds: Int64,
        selectedNativeSessionID: String?,
        sessions: [AgentSession],
        source: String,
        readOnly: Bool = true
    ) {
        self.providerID = providerID
        self.capabilities = capabilities
        self.observedAtMilliseconds = observedAtMilliseconds
        self.selectedNativeSessionID = selectedNativeSessionID
        self.sessions = sessions
        self.source = source
        self.readOnly = readOnly
    }

    public var selectedSessionID: String? {
        guard let selectedNativeSessionID else { return nil }
        return "\(providerID):\(selectedNativeSessionID)"
    }
}

public struct ProviderDescriptor: Equatable, Sendable {
    public let id: String
    public let displayName: String
    public let icon: DeckIcon
    public let capabilities: Set<ProviderCapability>

    public init(
        id: String,
        displayName: String,
        icon: DeckIcon,
        capabilities: Set<ProviderCapability>
    ) {
        self.id = id
        self.displayName = displayName
        self.icon = icon
        self.capabilities = capabilities
    }
}

public struct ProviderDiagnostics: Equatable, Sendable {
    public let providers: [String: [String]]
    public let unavailableProviders: [String: String]

    public init(
        providers: [String: [String]],
        unavailableProviders: [String: String]
    ) {
        self.providers = providers
        self.unavailableProviders = unavailableProviders
    }
}

public protocol AgentProvider: Sendable {
    var descriptor: ProviderDescriptor { get }
    func snapshot() async throws -> ProviderSnapshot
    func isFrontmost() async throws -> Bool
    func focus(nativeSessionID: String) async throws -> ProviderActionResult
    func openNew() async throws -> ProviderActionResult
    func executeCommand(
        nativeSessionID: String,
        commandID: CommandID
    ) async throws -> ProviderActionResult
    func recordHook(
        _ payload: ProviderHookPayload,
        observedAtMilliseconds: Int64
    ) async throws -> ActivityObservation
}

public extension AgentProvider {
    func focus(
        nativeSessionID: String
    ) async throws -> ProviderActionResult {
        throw ProviderOperationError.unsupported(
            "\(descriptor.displayName) does not support session focus"
        )
    }

    func openNew() async throws -> ProviderActionResult {
        throw ProviderOperationError.unsupported(
            "\(descriptor.displayName) does not support new sessions"
        )
    }

    func executeCommand(
        nativeSessionID: String,
        commandID: CommandID
    ) async throws -> ProviderActionResult {
        throw ProviderOperationError.unsupported(
            "\(descriptor.displayName) does not support \(commandID.rawValue)"
        )
    }

    func recordHook(
        _ payload: ProviderHookPayload,
        observedAtMilliseconds: Int64
    ) async throws -> ActivityObservation {
        throw ProviderOperationError.unsupported(
            "\(descriptor.displayName) does not accept hooks"
        )
    }
}
