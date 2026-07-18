import Foundation

public struct HealthResponse: Codable, Equatable, Sendable {
    public let status: String
    public let focusEnabled: Bool
    public let launchEnabled: Bool
    public let actionsEnabled: Bool
    public let providers: [String: [String]]
    public let unavailableProviders: [String: String]
    public let accessibilityTrusted: Bool

    public init(
        status: String = "ok",
        focusEnabled: Bool,
        launchEnabled: Bool,
        actionsEnabled: Bool,
        providers: [String: [String]],
        unavailableProviders: [String: String],
        accessibilityTrusted: Bool
    ) {
        self.status = status
        self.focusEnabled = focusEnabled
        self.launchEnabled = launchEnabled
        self.actionsEnabled = actionsEnabled
        self.providers = providers
        self.unavailableProviders = unavailableProviders
        self.accessibilityTrusted = accessibilityTrusted
    }

    private enum CodingKeys: String, CodingKey {
        case status
        case focusEnabled = "focus_enabled"
        case launchEnabled = "launch_enabled"
        case actionsEnabled = "actions_enabled"
        case providers
        case unavailableProviders = "unavailable_providers"
        case accessibilityTrusted = "accessibility_trusted"
    }
}

public struct APIErrorResponse: Codable, Equatable, Sendable {
    public let error: String
    public let retryable: Bool?

    public init(error: String, retryable: Bool? = nil) {
        self.error = error
        self.retryable = retryable
    }
}

public struct DeckActionRequest: Codable, Equatable, Sendable {
    public let clientID: String
    public let buttonID: String
    public let revision: Int

    public init(clientID: String, buttonID: String, revision: Int) {
        self.clientID = clientID
        self.buttonID = buttonID
        self.revision = revision
    }

    private enum CodingKeys: String, CodingKey {
        case clientID = "client_id"
        case buttonID = "button_id"
        case revision
    }
}

public struct DeckActivationResponse: Codable, Equatable, Sendable {
    public let accepted: Bool
    public let action: DeckAction
    public let snapshot: DeckSnapshot

    public init(
        accepted: Bool,
        action: DeckAction,
        snapshot: DeckSnapshot
    ) {
        self.accepted = accepted
        self.action = action
        self.snapshot = snapshot
    }
}
