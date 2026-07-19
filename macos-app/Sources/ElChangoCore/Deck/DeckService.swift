import Foundation

public enum DeckServiceError: LocalizedError, Equatable {
    case invalidClientID
    case invalidAction(String)

    public var errorDescription: String? {
        switch self {
        case .invalidClientID:
            "client_id must be 1-128 ASCII characters using only letters, numbers, '.', '_', ':', or '-'"
        case .invalidAction(let message):
            message
        }
    }
}

public actor DeckService {
    public static let defaultClientID = "web"
    public static let maximumClientIDLength = 128

    private enum Picker: Equatable {
        case newProvider
        case sessionIcon(sessionID: String, page: Int)
        case slotCommand(index: Int)
    }

    private struct CombinedSnapshot {
        let observedAtMilliseconds: Int64
        let selectedSessionID: String?
        let sessions: [AgentSession]
        let source: String
        let readOnly: Bool
        let commandTarget: AgentSession?
    }

    private struct ProviderTask: Sendable {
        let index: Int
        let providerID: String
        let provider: any AgentProvider
    }

    private struct ProviderSnapshotResult: Sendable {
        let index: Int
        let providerID: String
        let snapshot: ProviderSnapshot?
        let errorDescription: String?
    }

    private struct RenderSignature: Equatable {
        let selectedSessionID: String?
        let sessions: [AgentSession]
        let sessionOrder: [String?]
        let pageIndex: Int
        let picker: Picker?
        let preferences: DeckPreferences
        let newSessionProviders: [ProviderDescriptor]
        let source: String
        let readOnly: Bool
    }

    private final class ClientState {
        var pageIndex = 0
        var picker: Picker?
        var revision = 0
        var signature: RenderSignature?
        var lastAccessed: TimeInterval

        init(lastAccessed: TimeInterval) {
            self.lastAccessed = lastAccessed
        }
    }

    private let providers: [String: any AgentProvider]
    private let providerOrder: [String]
    private let preferences: PreferencesStore
    private let maximumClientStates: Int
    private let clientStateTTL: TimeInterval
    private let monotonicNow: @Sendable () -> TimeInterval
    private let wallClockNow: @Sendable () -> Date

    private var providerErrors: [String: String] = [:]
    private var sessionOrder: [String?] = []
    private var refreshRequested = true
    private var clientStates: [String: ClientState] = [:]
    private var clientAccessOrder: [String] = []

    public init(
        providers: [any AgentProvider] = [],
        preferences: PreferencesStore,
        maximumClientStates: Int = 64,
        clientStateTTL: TimeInterval = 30 * 60,
        monotonicNow: @Sendable @escaping () -> TimeInterval = {
            ProcessInfo.processInfo.systemUptime
        },
        wallClockNow: @Sendable @escaping () -> Date = Date.init
    ) throws {
        guard maximumClientStates > 0 else {
            throw DeckServiceError.invalidAction(
                "maximum client states must be positive"
            )
        }
        guard clientStateTTL > 0 else {
            throw DeckServiceError.invalidAction(
                "client state TTL must be positive"
            )
        }

        var registry: [String: any AgentProvider] = [:]
        var providerOrder: [String] = []
        for provider in providers {
            let id = provider.descriptor.id
            guard registry[id] == nil else {
                throw DeckServiceError.invalidAction(
                    "provider IDs must be unique"
                )
            }
            registry[id] = provider
            providerOrder.append(id)
        }
        self.providers = registry
        self.providerOrder = providerOrder
        self.preferences = preferences
        self.maximumClientStates = maximumClientStates
        self.clientStateTTL = clientStateTTL
        self.monotonicNow = monotonicNow
        self.wallClockNow = wallClockNow
    }

    public func snapshot(
        clientID: String = DeckService.defaultClientID
    ) async throws -> DeckSnapshot {
        try Self.validateClientID(clientID)
        let combined = await combinedSnapshot()
        let preferences = await preferences.snapshot()
        let state = clientState(for: clientID)
        let sessionsByID = updateSessionOrder(from: combined.sessions)
        let count = pageCount
        state.pageIndex = min(state.pageIndex, count - 1)
        let visibleSessions = visibleSessions(
            from: sessionsByID,
            pageIndex: state.pageIndex
        )
        let descriptors = newSessionProviders
        let signature = RenderSignature(
            selectedSessionID: combined.selectedSessionID,
            sessions: combined.sessions,
            sessionOrder: sessionOrder,
            pageIndex: state.pageIndex,
            picker: state.picker,
            preferences: preferences,
            newSessionProviders: descriptors,
            source: combined.source,
            readOnly: combined.readOnly
        )
        if signature != state.signature {
            state.revision += 1
            state.signature = signature
        }

        let page: Int
        let renderedPageCount: Int
        let hasPrevious: Bool
        let hasNext: Bool
        let buttons: [DeckButton]

        switch state.picker {
        case .newProvider:
            buttons = try DeckLayout.providerPicker(providers: descriptors)
            page = 1
            renderedPageCount = 1
            hasPrevious = false
            hasNext = false
        case .sessionIcon(_, let pickerPage):
            let picker = try DeckLayout.iconPicker(pageIndex: pickerPage)
            buttons = picker.buttons
            page = pickerPage + 1
            renderedPageCount = picker.pageCount
            hasPrevious = pickerPage > 0
            hasNext = pickerPage + 1 < picker.pageCount
        case .slotCommand:
            buttons = try DeckLayout.commandPicker()
            page = 1
            renderedPageCount = 1
            hasPrevious = false
            hasNext = false
        case nil:
            page = state.pageIndex + 1
            renderedPageCount = count
            hasPrevious = state.pageIndex > 0
            hasNext = state.pageIndex + 1 < count
            buttons = DeckLayout.sessionButtons(
                sessions: visibleSessions,
                page: page,
                hasNext: hasNext,
                launchEnabled: !descriptors.isEmpty,
                preferences: preferences,
                commandTarget: combined.commandTarget
            )
        }

        guard buttons.count == DeckLayout.totalButtons else {
            throw DeckServiceError.invalidAction(
                "deck invariant violated: expected 15 buttons"
            )
        }
        return DeckSnapshot(
            revision: state.revision,
            observedAtMilliseconds: combined.observedAtMilliseconds,
            source: combined.source,
            readOnly: combined.readOnly,
            selectedSessionID: combined.selectedSessionID,
            page: page,
            pageCount: renderedPageCount,
            hasPrevious: hasPrevious,
            hasNext: hasNext,
            buttons: buttons
        )
    }

    public func refresh(clientID: String) async throws -> DeckSnapshot {
        try Self.validateClientID(clientID)
        refreshRequested = true
        clientState(for: clientID).pageIndex = 0
        return try await snapshot(clientID: clientID)
    }

    public func previousPage(clientID: String) async throws -> DeckSnapshot {
        try Self.validateClientID(clientID)
        let state = clientState(for: clientID)
        guard state.pageIndex > 0 else {
            throw DeckServiceError.invalidAction(
                "the deck is already on the first page"
            )
        }
        state.pageIndex -= 1
        return try await snapshot(clientID: clientID)
    }

    public func nextPage(clientID: String) async throws -> DeckSnapshot {
        try Self.validateClientID(clientID)
        let state = clientState(for: clientID)
        guard state.pageIndex + 1 < pageCount else {
            throw DeckServiceError.invalidAction(
                "the deck is already on the last page"
            )
        }
        state.pageIndex += 1
        return try await snapshot(clientID: clientID)
    }

    public func chooseNewProvider(
        clientID: String
    ) async throws -> DeckSnapshot {
        try Self.validateClientID(clientID)
        _ = await combinedSnapshot()
        guard !newSessionProviders.isEmpty else {
            throw DeckServiceError.invalidAction(
                "no provider supports new sessions"
            )
        }
        clientState(for: clientID).picker = .newProvider
        return try await snapshot(clientID: clientID)
    }

    public func cancelNewSession(
        clientID: String
    ) async throws -> DeckSnapshot {
        try Self.validateClientID(clientID)
        let state = clientState(for: clientID)
        guard state.picker == .newProvider else {
            throw DeckServiceError.invalidAction(
                "new session provider chooser is not open"
            )
        }
        state.picker = nil
        return try await snapshot(clientID: clientID)
    }

    public func completeNewSession(
        clientID: String
    ) async throws -> DeckSnapshot {
        try Self.validateClientID(clientID)
        clientState(for: clientID).picker = nil
        return try await snapshot(clientID: clientID)
    }

    public func chooseSessionIcon(
        clientID: String,
        sessionID: String
    ) async throws -> DeckSnapshot {
        try Self.validateClientID(clientID)
        clientState(for: clientID).picker = .sessionIcon(
            sessionID: sessionID,
            page: 0
        )
        return try await snapshot(clientID: clientID)
    }

    public func chooseSlotCommand(
        clientID: String,
        index: Int
    ) async throws -> DeckSnapshot {
        try Self.validateClientID(clientID)
        guard 0..<3 ~= index else {
            throw DeckServiceError.invalidAction(
                "action slot must be between 0 and 2"
            )
        }
        clientState(for: clientID).picker = .slotCommand(index: index)
        return try await snapshot(clientID: clientID)
    }

    public func selectSessionIcon(
        clientID: String,
        icon: DeckIcon
    ) async throws -> DeckSnapshot {
        try Self.validateClientID(clientID)
        let state = clientState(for: clientID)
        guard case .sessionIcon(let sessionID, _) = state.picker else {
            throw DeckServiceError.invalidAction(
                "session icon picker is not open"
            )
        }
        state.picker = nil
        try await preferences.setSessionIcon(
            sessionID: sessionID,
            icon: icon
        )
        return try await snapshot(clientID: clientID)
    }

    public func selectSlotCommand(
        clientID: String,
        commandID: CommandID
    ) async throws -> DeckSnapshot {
        try Self.validateClientID(clientID)
        let state = clientState(for: clientID)
        guard case .slotCommand(let index) = state.picker else {
            throw DeckServiceError.invalidAction(
                "command picker is not open"
            )
        }
        state.picker = nil
        try await preferences.setActionSlot(
            index: index,
            commandID: commandID
        )
        return try await snapshot(clientID: clientID)
    }

    public func cancelPicker(
        clientID: String
    ) async throws -> DeckSnapshot {
        try Self.validateClientID(clientID)
        let state = clientState(for: clientID)
        guard state.picker != nil else {
            throw DeckServiceError.invalidAction("no picker is open")
        }
        state.picker = nil
        return try await snapshot(clientID: clientID)
    }

    public func previousPickerPage(
        clientID: String
    ) async throws -> DeckSnapshot {
        try Self.validateClientID(clientID)
        let state = clientState(for: clientID)
        guard case .sessionIcon(let sessionID, let page) = state.picker,
            page > 0
        else {
            throw DeckServiceError.invalidAction(
                "picker is already on its first page"
            )
        }
        state.picker = .sessionIcon(
            sessionID: sessionID,
            page: page - 1
        )
        return try await snapshot(clientID: clientID)
    }

    public func nextPickerPage(
        clientID: String
    ) async throws -> DeckSnapshot {
        try Self.validateClientID(clientID)
        let state = clientState(for: clientID)
        let count = max(
            1,
            (DeckIcon.personalizationOptions.count
                + DeckLayout.sessionSlots - 1) / DeckLayout.sessionSlots
        )
        guard case .sessionIcon(let sessionID, let page) = state.picker,
            page + 1 < count
        else {
            throw DeckServiceError.invalidAction(
                "picker is already on its last page"
            )
        }
        state.picker = .sessionIcon(
            sessionID: sessionID,
            page: page + 1
        )
        return try await snapshot(clientID: clientID)
    }

    public func latestProviderErrors() -> [String: String] {
        providerErrors
    }

    public func providerDiagnostics() async -> ProviderDiagnostics {
        _ = await combinedSnapshot()
        let entries: [(String, [String])] = providerOrder.compactMap {
            providerID in
            guard providerErrors[providerID] == nil else {
                return nil
            }
            guard let descriptor = providers[providerID]?.descriptor else {
                return nil
            }
            return (
                providerID,
                descriptor.capabilities.map(\.rawValue).sorted()
            )
        }
        let capabilities = Dictionary(
            uniqueKeysWithValues: entries
        )
        return ProviderDiagnostics(
            providers: capabilities,
            unavailableProviders: providerErrors
        )
    }

    public func focusSession(
        sessionID: String
    ) async throws -> ProviderActionResult {
        let combined = await combinedSnapshot()
        let matches = combined.sessions.filter {
            $0.id == sessionID
                && $0.capabilities.contains(.focusSession)
        }
        guard matches.count == 1,
            let target = matches.first,
            let provider = providers[target.providerID]
        else {
            throw DeckServiceError.invalidAction(
                "session is not focusable in the current snapshot"
            )
        }
        return try await provider.focus(
            nativeSessionID: target.nativeID
        )
    }

    public func openNew(
        providerID: String
    ) async throws -> ProviderActionResult {
        _ = await combinedSnapshot()
        guard let provider = providers[providerID],
            providerErrors[providerID] == nil,
            provider.descriptor.capabilities.contains(.newSession)
        else {
            throw DeckServiceError.invalidAction(
                "provider does not support new sessions"
            )
        }
        return try await provider.openNew()
    }

    public func executeCommand(
        sessionID: String,
        commandID: CommandID
    ) async throws -> ProviderActionResult {
        let combined = await combinedSnapshot()
        guard let target = combined.commandTarget,
            target.id == sessionID,
            target.commands.contains(commandID),
            let provider = providers[target.providerID]
        else {
            throw DeckServiceError.invalidAction(
                "command has no verified foreground target"
            )
        }
        return try await provider.executeCommand(
            nativeSessionID: target.nativeID,
            commandID: commandID
        )
    }

    public func recordHook(
        providerID: String,
        payload: ProviderHookPayload,
        observedAtMilliseconds: Int64
    ) async throws -> ActivityObservation {
        guard let provider = providers[providerID] else {
            throw ProviderOperationError.unsupported(
                "provider does not accept hooks: \(providerID)"
            )
        }
        return try await provider.recordHook(
            payload,
            observedAtMilliseconds: observedAtMilliseconds
        )
    }

    public func activeClientCount() -> Int {
        expireClientStates(at: monotonicNow())
        return clientStates.count
    }

    public static func validateClientID(_ value: String) throws {
        guard 1...maximumClientIDLength ~= value.utf8.count,
            value.unicodeScalars.allSatisfy({ scalar in
                scalar.isASCII
                    && (CharacterSet.alphanumerics.contains(scalar)
                        || "._:-".unicodeScalars.contains(scalar))
            })
        else {
            throw DeckServiceError.invalidClientID
        }
    }

    private func combinedSnapshot() async -> CombinedSnapshot {
        var indexedSnapshots: [(Int, ProviderSnapshot)] = []
        var errors: [String: String] = [:]
        let providerTasks = providerOrder.enumerated().compactMap {
            index, providerID -> ProviderTask? in
            guard let provider = providers[providerID] else { return nil }
            return ProviderTask(
                index: index,
                providerID: providerID,
                provider: provider
            )
        }
        let results = await Task.detached {
            await withTaskGroup(
                of: ProviderSnapshotResult.self,
                returning: [ProviderSnapshotResult].self
            ) { group in
                for task in providerTasks {
                    group.addTask {
                        do {
                            return ProviderSnapshotResult(
                                index: task.index,
                                providerID: task.providerID,
                                snapshot: try await task.provider.snapshot(),
                                errorDescription: nil
                            )
                        } catch {
                            return ProviderSnapshotResult(
                                index: task.index,
                                providerID: task.providerID,
                                snapshot: nil,
                                errorDescription: error.localizedDescription
                            )
                        }
                    }
                }
                return await group.reduce(into: []) { $0.append($1) }
            }
        }.value
        for result in results {
            if let snapshot = result.snapshot {
                guard snapshot.providerID == result.providerID else {
                    errors[result.providerID] =
                        "provider snapshot ID mismatch"
                    continue
                }
                indexedSnapshots.append((result.index, snapshot))
            } else if let error = result.errorDescription {
                errors[result.providerID] = error
            }
        }
        let snapshots = indexedSnapshots.sorted { $0.0 < $1.0 }.map(\.1)
        providerErrors = errors

        guard !snapshots.isEmpty else {
            let source =
                errors.isEmpty
                ? "no providers available"
                : errors.keys.sorted().map { providerID in
                    "\(providerID)=unavailable: \(errors[providerID]!)"
                }.joined(separator: ";")
            return CombinedSnapshot(
                observedAtMilliseconds: Int64(
                    wallClockNow().timeIntervalSince1970 * 1_000
                ),
                selectedSessionID: nil,
                sessions: [],
                source: source,
                readOnly: true,
                commandTarget: nil
            )
        }

        var commandTargets: [AgentSession] = []
        for snapshot in snapshots
        where snapshot.capabilities.contains(.executeCommand) {
            guard let provider = providers[snapshot.providerID],
                let selectedID = snapshot.selectedNativeSessionID,
                (try? await provider.isFrontmost()) == true
            else {
                continue
            }
            commandTargets.append(
                contentsOf: snapshot.sessions.filter {
                    $0.nativeID == selectedID
                }
            )
        }

        return CombinedSnapshot(
            observedAtMilliseconds: snapshots.map(
                \.observedAtMilliseconds
            ).max() ?? 0,
            selectedSessionID: snapshots.compactMap(
                \.selectedSessionID
            ).first,
            sessions: snapshots.flatMap(\.sessions),
            source: (snapshots.map { "\($0.providerID)=\($0.source)" }
                + errors.keys.sorted().map { providerID in
                    "\(providerID)=unavailable: \(errors[providerID]!)"
                }).joined(separator: ";"),
            readOnly: snapshots.allSatisfy(\.readOnly),
            commandTarget: commandTargets.count == 1
                ? commandTargets[0]
                : nil
        )
    }

    private var newSessionProviders: [ProviderDescriptor] {
        providerOrder
            .filter { providerErrors[$0] == nil }
            .compactMap { providers[$0]?.descriptor }
            .filter { $0.capabilities.contains(.newSession) }
    }

    private func clientState(for clientID: String) -> ClientState {
        let now = monotonicNow()
        expireClientStates(at: now)
        if let existing = clientStates[clientID] {
            existing.lastAccessed = now
            clientAccessOrder.removeAll { $0 == clientID }
            clientAccessOrder.append(clientID)
            return existing
        }
        if clientStates.count >= maximumClientStates,
            let oldest = clientAccessOrder.first
        {
            clientStates.removeValue(forKey: oldest)
            clientAccessOrder.removeFirst()
        }
        let state = ClientState(lastAccessed: now)
        clientStates[clientID] = state
        clientAccessOrder.append(clientID)
        return state
    }

    private func expireClientStates(at now: TimeInterval) {
        let cutoff = now - clientStateTTL
        let expired = clientStates.compactMap { clientID, state in
            state.lastAccessed <= cutoff ? clientID : nil
        }
        for clientID in expired {
            clientStates.removeValue(forKey: clientID)
            clientAccessOrder.removeAll { $0 == clientID }
        }
    }

    private func updateSessionOrder(
        from sessions: [AgentSession]
    ) -> [String: AgentSession] {
        let sessionsByID = Dictionary(
            uniqueKeysWithValues: sessions.map { ($0.id, $0) }
        )
        let sorted = sessions.sorted {
            if $0.lastActivityAtMilliseconds
                != $1.lastActivityAtMilliseconds
            {
                return $0.lastActivityAtMilliseconds
                    > $1.lastActivityAtMilliseconds
            }
            return $0.id < $1.id
        }
        if refreshRequested {
            sessionOrder = sorted.map(\.id)
            refreshRequested = false
        } else {
            for index in sessionOrder.indices {
                if let sessionID = sessionOrder[index],
                    sessionsByID[sessionID] == nil
                {
                    sessionOrder[index] = nil
                }
            }
            let knownIDs = Set(sessionOrder.compactMap { $0 })
            let newIDs: [String?] =
                sorted
                .map(\.id)
                .filter { !knownIDs.contains($0) }
                .map(Optional.some)
            sessionOrder.append(
                contentsOf: newIDs
            )
        }
        return sessionsByID
    }

    private func visibleSessions(
        from sessionsByID: [String: AgentSession],
        pageIndex: Int
    ) -> [AgentSession?] {
        let start = pageIndex * DeckLayout.sessionSlots
        let end = min(start + DeckLayout.sessionSlots, sessionOrder.count)
        var ids =
            start < end
            ? Array(sessionOrder[start..<end])
            : []
        ids.append(
            contentsOf: repeatElement(
                nil,
                count: DeckLayout.sessionSlots - ids.count
            )
        )
        return ids.map { sessionID in
            sessionID.flatMap { sessionsByID[$0] }
        }
    }

    private var pageCount: Int {
        max(
            1,
            (sessionOrder.count + DeckLayout.sessionSlots - 1) / DeckLayout.sessionSlots
        )
    }
}
