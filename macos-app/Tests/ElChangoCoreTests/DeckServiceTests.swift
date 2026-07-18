import ElChangoCore
import Foundation
import Testing

@Suite("Native deck state")
struct DeckServiceTests {
    @Test("empty registry preserves the fifteen-key contract")
    func noProviders() async throws {
        let context = try TestContext()
        defer { context.remove() }
        let service = try DeckService(preferences: context.preferences)

        let snapshot = try await service.snapshot()

        #expect(snapshot.buttons.count == 15)
        #expect(snapshot.source == "no providers available")
        #expect(snapshot.buttons[0].action == .chooseNewProvider)
        #expect(snapshot.buttons[11].label == "Accept")
        #expect(snapshot.buttons[12].label == "Commit Push")
        #expect(snapshot.buttons[13].label == "Open PR")
        #expect(!snapshot.buttons[14].enabled)
    }

    @Test("revisions change only when rendered content changes")
    func stableRevision() async throws {
        let context = try TestContext()
        defer { context.remove() }
        let provider = MutableProvider(snapshot: makeSnapshot(count: 1))
        let service = try DeckService(
            providers: [provider],
            preferences: context.preferences
        )

        let first = try await service.snapshot()
        let second = try await service.snapshot()
        await provider.setSnapshot(
            makeSnapshot(count: 1, observedAt: 2_000)
        )
        let third = try await service.snapshot()
        await provider.setSnapshot(makeSnapshot(count: 2))
        let fourth = try await service.snapshot()

        #expect(second.revision == first.revision)
        #expect(third.revision == second.revision)
        #expect(fourth.revision == third.revision + 1)
    }

    @Test("clients navigate independently")
    func independentPagination() async throws {
        let context = try TestContext()
        defer { context.remove() }
        let provider = MutableProvider(snapshot: makeSnapshot(count: 11))
        let service = try DeckService(
            providers: [provider],
            preferences: context.preferences
        )

        let webFirst = try await service.snapshot(clientID: "web")
        let hardwareFirst = try await service.snapshot(
            clientID: "streamdeck:serial-1"
        )
        let hardwareSecond = try await service.nextPage(
            clientID: "streamdeck:serial-1"
        )
        let webSecond = try await service.snapshot(clientID: "web")

        #expect(webFirst.page == 1)
        #expect(webSecond.page == 1)
        #expect(hardwareFirst.page == 1)
        #expect(hardwareSecond.page == 2)
        #expect(webSecond.revision == webFirst.revision)
        #expect(hardwareSecond.revision == hardwareFirst.revision + 1)
    }

    @Test("icon and command pickers are client-scoped and persist")
    func customizationPickers() async throws {
        let context = try TestContext()
        defer { context.remove() }
        let provider = MutableProvider(
            snapshot: makeSnapshot(count: 1, selected: true),
            frontmost: true
        )
        let service = try DeckService(
            providers: [provider],
            preferences: context.preferences
        )
        let sessionID = "test:session-0"

        let iconPicker = try await service.chooseSessionIcon(
            clientID: "web",
            sessionID: sessionID
        )
        let hardware = try await service.snapshot(clientID: "streamdeck")
        #expect(iconPicker.buttons[0].action == .setSessionIcon)
        #expect(hardware.buttons[0].kind == .session)

        let customized = try await service.selectSessionIcon(
            clientID: "web",
            icon: .robot
        )
        #expect(customized.buttons[0].icon == .robot)

        let commandPicker = try await service.chooseSlotCommand(
            clientID: "web",
            index: 0
        )
        #expect(
            commandPicker.buttons.contains {
                $0.commandID == .compact
            }
        )
        let reassigned = try await service.selectSlotCommand(
            clientID: "web",
            commandID: .compact
        )
        #expect(reassigned.buttons[11].label == "Compact")

        let reloaded = try PreferencesStore(url: context.preferencesURL)
        #expect(
            await reloaded.snapshot().sessionIcons[sessionID] == .robot
        )
        #expect(await reloaded.snapshot().actionSlots[0] == .compact)
    }

    @Test("only one frontmost selected target enables commands")
    func commandTarget() async throws {
        let context = try TestContext()
        defer { context.remove() }
        let provider = MutableProvider(
            snapshot: makeSnapshot(count: 1, selected: true),
            frontmost: true
        )
        let service = try DeckService(
            providers: [provider],
            preferences: context.preferences
        )

        let snapshot = try await service.snapshot()

        #expect(snapshot.buttons[11].enabled)
        #expect(snapshot.buttons[12].enabled)
        #expect(snapshot.buttons[13].enabled)
        #expect(snapshot.buttons[11].sessionID == "test:session-0")
    }

    @Test("provider failures do not hide healthy providers")
    func isolatedProviderFailure() async throws {
        let context = try TestContext()
        defer { context.remove() }
        let healthy = MutableProvider(snapshot: makeSnapshot(count: 1))
        let failing = FailingProvider()
        let service = try DeckService(
            providers: [healthy, failing],
            preferences: context.preferences
        )

        let snapshot = try await service.snapshot()
        let errors = await service.latestProviderErrors()

        #expect(snapshot.buttons[0].sessionID == "test:session-0")
        #expect(snapshot.source.contains("failing=unavailable"))
        #expect(errors["failing"] == "inventory unavailable")
    }

    @Test("removed sessions leave holes until refresh")
    func stableSlotsUntilRefresh() async throws {
        let context = try TestContext()
        defer { context.remove() }
        let provider = MutableProvider(snapshot: makeSnapshot(count: 3))
        let service = try DeckService(
            providers: [provider],
            preferences: context.preferences
        )
        _ = try await service.snapshot()
        let reduced = makeSnapshot(count: 3)
        await provider.setSnapshot(
            ProviderSnapshot(
                providerID: reduced.providerID,
                capabilities: reduced.capabilities,
                observedAtMilliseconds: 2_000,
                selectedNativeSessionID: nil,
                sessions: reduced.sessions.filter {
                    $0.nativeID != "session-1"
                },
                source: reduced.source
            )
        )

        let withHole = try await service.snapshot()
        let refreshed = try await service.refresh(clientID: "web")

        #expect(withHole.buttons[1].sessionID == nil)
        #expect(refreshed.buttons[0].sessionID == "test:session-0")
        #expect(refreshed.buttons[1].sessionID == "test:session-2")
    }

    @Test("expired and evicted clients restart from page one")
    func clientStateBounds() async throws {
        let context = try TestContext()
        defer { context.remove() }
        let clock = TestClock()
        let provider = MutableProvider(snapshot: makeSnapshot(count: 11))
        let service = try DeckService(
            providers: [provider],
            preferences: context.preferences,
            maximumClientStates: 2,
            clientStateTTL: 10,
            monotonicNow: { clock.now }
        )
        _ = try await service.snapshot(clientID: "expiring")
        _ = try await service.nextPage(clientID: "expiring")
        clock.advance(by: 11)
        let expired = try await service.snapshot(clientID: "expiring")
        #expect(expired.page == 1)
        #expect(expired.revision == 1)

        _ = try await service.snapshot(clientID: "oldest")
        clock.advance(by: 1)
        _ = try await service.snapshot(clientID: "newest")
        clock.advance(by: 1)
        _ = try await service.snapshot(clientID: "third")
        let recreated = try await service.snapshot(clientID: "oldest")

        #expect(recreated.page == 1)
        #expect(recreated.revision == 1)
        #expect(await service.activeClientCount() == 2)
    }
}

private struct TestContext {
    let directory: URL
    let preferencesURL: URL
    let preferences: PreferencesStore

    init() throws {
        directory = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(
            at: directory,
            withIntermediateDirectories: true
        )
        preferencesURL = directory.appendingPathComponent("preferences.json")
        preferences = try PreferencesStore(url: preferencesURL)
    }

    func remove() {
        try? FileManager.default.removeItem(at: directory)
    }
}

private actor MutableProvider: AgentProvider {
    nonisolated let descriptor = ProviderDescriptor(
        id: "test",
        displayName: "Test",
        icon: .terminal,
        capabilities: [.focusSession, .newSession, .executeCommand]
    )

    private var currentSnapshot: ProviderSnapshot
    private let frontmost: Bool

    init(snapshot: ProviderSnapshot, frontmost: Bool = false) {
        self.currentSnapshot = snapshot
        self.frontmost = frontmost
    }

    func snapshot() -> ProviderSnapshot {
        currentSnapshot
    }

    func isFrontmost() -> Bool {
        frontmost
    }

    func setSnapshot(_ snapshot: ProviderSnapshot) {
        currentSnapshot = snapshot
    }
}

private struct FailingProvider: AgentProvider {
    let descriptor = ProviderDescriptor(
        id: "failing",
        displayName: "Failing",
        icon: .bug,
        capabilities: []
    )

    func snapshot() async throws -> ProviderSnapshot {
        throw TestProviderError.inventoryUnavailable
    }

    func isFrontmost() -> Bool {
        false
    }
}

private enum TestProviderError: LocalizedError {
    case inventoryUnavailable

    var errorDescription: String? {
        "inventory unavailable"
    }
}

private final class TestClock: @unchecked Sendable {
    private let lock = NSLock()
    private var value: TimeInterval = 0

    var now: TimeInterval {
        lock.withLock { value }
    }

    func advance(by interval: TimeInterval) {
        lock.withLock {
            value += interval
        }
    }
}

private func makeSnapshot(
    count: Int,
    observedAt: Int64 = 1_000,
    selected: Bool = false
) -> ProviderSnapshot {
    let sessions = (0..<count).map { index in
        AgentSession(
            providerID: "test",
            nativeID: "session-\(index)",
            capabilities: [.focusSession, .executeCommand],
            icon: .terminal,
            title: "Session \(index)",
            workspaceID: "workspace-\(index)",
            workspacePath: "/tmp/workspace-\(index)",
            state: .idle,
            confidence: .observed,
            stateDetail: "",
            selected: selected && index == 0,
            lastActivityAtMilliseconds: Int64(count - index),
            commands: [.accept, .commitPush, .createPR, .compact]
        )
    }
    return ProviderSnapshot(
        providerID: "test",
        capabilities: [.focusSession, .newSession, .executeCommand],
        observedAtMilliseconds: observedAt,
        selectedNativeSessionID: selected ? "session-0" : nil,
        sessions: sessions,
        source: "test"
    )
}
