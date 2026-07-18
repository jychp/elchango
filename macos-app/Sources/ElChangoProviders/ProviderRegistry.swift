import ElChangoCore

public struct ProviderRegistry: Sendable {
    public let providers: [any AgentProvider]
    public let unavailableProviders: [String: String]

    public init(
        providers: [any AgentProvider] = [CursorProvider()],
        unavailableProviders: [String: String] = [
            "claude-code": "provider not migrated to native host",
        ]
    ) {
        self.providers = providers
        self.unavailableProviders = unavailableProviders
    }
}
