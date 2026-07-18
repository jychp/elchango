import ElChangoCore

public struct ProviderRegistry: Sendable {
    public let providers: [any AgentProvider]
    public let unavailableProviders: [String: String]

    public init(
        providers: [any AgentProvider]? = nil,
        unavailableProviders: [String: String] = [:]
    ) {
        if let providers {
            self.providers = providers
        } else {
            let automation = NativeAutomation()
            let actionGate = PrivilegedActionGate()
            self.providers = [
                CursorProvider(
                    automation: automation,
                    actionGate: actionGate
                ),
                ClaudeCodeProvider(
                    automation: automation,
                    actionGate: actionGate
                ),
            ]
        }
        self.unavailableProviders = unavailableProviders
    }
}
