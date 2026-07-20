@preconcurrency import ApplicationServices
import ElChangoCore
import Foundation
import SQLite3
import Testing

@testable import ElChangoProviders

@Suite("Native Cursor inventory")
struct CursorProviderTests {
    @Test("matches the shared Python inventory fixture")
    func sharedFixtureParity() async throws {
        let fixture = try Fixture()
        let databaseURL = try fixture.makeDatabase()
        let originalDatabase = try Data(contentsOf: databaseURL)
        let provider = CursorProvider(
            databaseURL: databaseURL,
            workspaceStorageURL: fixture.root.appendingPathComponent(
                "workspaceStorage"
            ),
            activeSignalTTLMilliseconds: 1_000,
            clock: { 1_000 }
        )

        let snapshot = try await provider.snapshot()
        let expected = try JSONDecoder().decode(
            ExpectedInventory.self,
            from: Data(
                contentsOf: fixture.root.appendingPathComponent(
                    "expected-inventory.json"
                )
            )
        )
        let actual = ExpectedInventory(snapshot: snapshot)

        #expect(actual == expected)
        #expect(
            snapshot.capabilities == [
                .focusSession, .newSession, .executeCommand,
            ])
        #expect(
            snapshot.sessions.allSatisfy {
                $0.capabilities == snapshot.capabilities
                    && $0.commands == CursorProvider.commands
            }
        )
        #expect(try Data(contentsOf: databaseURL) == originalDatabase)
        #expect(
            !FileManager.default.fileExists(
                atPath: databaseURL.path + "-journal"
            )
        )
    }

    @Test("fails explicitly when Cursor changes required tables")
    func rejectsUnknownSchema() async throws {
        let fixture = try Fixture()
        let databaseURL = try fixture.makeDatabase(sql: "")
        let provider = CursorProvider(
            databaseURL: databaseURL,
            workspaceStorageURL: fixture.root
        )

        await #expect(throws: CursorProviderError.self) {
            try await provider.snapshot()
        }
    }

    @Test("fails explicitly when a required column disappears")
    func rejectsMissingColumn() async throws {
        let fixture = try Fixture()
        let databaseURL = try fixture.makeDatabase(
            sql: """
                CREATE TABLE ItemTable (key TEXT);
                CREATE TABLE composerHeaders (
                    composerId TEXT,
                    workspaceId TEXT,
                    lastUpdatedAt INTEGER,
                    isArchived INTEGER,
                    isSubagent INTEGER,
                    value BLOB
                );
                CREATE TABLE cursorDiskKV (key TEXT, value BLOB);
                """
        )
        let provider = CursorProvider(
            databaseURL: databaseURL,
            workspaceStorageURL: fixture.root
        )

        await #expect(throws: CursorProviderError.self) {
            try await provider.snapshot()
        }
    }

    @Test("an unresolved selected session fails closed")
    func unresolvedSelection() async throws {
        let fixture = try Fixture()
        let databaseURL = try fixture.makeDatabase()
        try fixture.execute(
            """
            UPDATE ItemTable
            SET value = 'composer-2'
            WHERE key = 'cursor/glass.selectedAgent'
            """,
            at: databaseURL
        )
        let provider = CursorProvider(
            databaseURL: databaseURL,
            workspaceStorageURL: fixture.temporaryRoot,
            activeSignalTTLMilliseconds: 1_000,
            clock: { 1_000 }
        )

        let snapshot = try await provider.snapshot()

        #expect(snapshot.selectedNativeSessionID == nil)
        #expect(snapshot.selectedSessionID == nil)
        #expect(snapshot.sessions.allSatisfy { !$0.selected })
        #expect(
            snapshot.sessions.contains {
                $0.nativeID == "composer-2" && $0.workspacePath == nil
            }
        )
    }

    @Test("persisted activity expires instead of remaining working")
    func staleActivity() async throws {
        let fixture = try Fixture()
        let provider = CursorProvider(
            databaseURL: try fixture.makeDatabase(),
            workspaceStorageURL: fixture.root.appendingPathComponent(
                "workspaceStorage"
            ),
            activeSignalTTLMilliseconds: 1_000,
            clock: { 2_000 }
        )

        let session = try #require(
            try await provider.snapshot().sessions.first
        )

        #expect(session.state == .idle)
        #expect(session.confidence == .persisted)
        #expect(session.stateDetail == "stale tool loading signal ignored")
    }

    @Test("a fresh pending plan requires user attention")
    func pendingPlan() async throws {
        let fixture = try Fixture()
        let databaseURL = try fixture.makeDatabase()
        try fixture.execute(
            """
            UPDATE cursorDiskKV
            SET value = '{"hasPendingPlan": true}'
            WHERE key = 'composerData:composer-1'
            """,
            at: databaseURL
        )
        let provider = CursorProvider(
            databaseURL: databaseURL,
            workspaceStorageURL: fixture.root.appendingPathComponent(
                "workspaceStorage"
            ),
            activeSignalTTLMilliseconds: 1_000,
            clock: { 1_000 }
        )

        let session = try #require(
            try await provider.snapshot().sessions.first
        )

        #expect(session.state == .waiting)
        #expect(session.confidence == .candidate)
        #expect(session.stateDetail == "user action or plan pending")
    }

    @Test("a composer error surfaces as a terminal error state")
    func composerError() async throws {
        let fixture = try Fixture()
        let databaseURL = try fixture.makeDatabase()
        try fixture.execute(
            """
            UPDATE cursorDiskKV
            SET value = '{"status": "error"}'
            WHERE key = 'composerData:composer-1'
            """,
            at: databaseURL
        )
        let provider = CursorProvider(
            databaseURL: databaseURL,
            workspaceStorageURL: fixture.root.appendingPathComponent(
                "workspaceStorage"
            ),
            activeSignalTTLMilliseconds: 1_000,
            clock: { 1_000 }
        )

        let session = try #require(
            try await provider.snapshot().sessions.first {
                $0.nativeID == "composer-1"
            }
        )

        #expect(session.state == .error)
        #expect(session.confidence == .candidate)
        #expect(session.stateDetail == "composer error")
    }

    @Test("reconciles a hook received before inventory persistence")
    func hookBeforeInventory() async throws {
        let fixture = try Fixture()
        let activity = CursorActivityStore()
        let databaseURL = fixture.temporaryRoot.appendingPathComponent(
            "state.vscdb"
        )
        let provider = CursorProvider(
            databaseURL: databaseURL,
            workspaceStorageURL: fixture.root.appendingPathComponent(
                "workspaceStorage"
            ),
            activityStore: activity,
            clock: { 200 }
        )
        _ = try await provider.recordHook(
            ProviderHookPayload(
                hookEventName: "stop",
                conversationID: "composer-1",
                generationID: "generation-1",
                status: "completed"
            ),
            observedAtMilliseconds: 150
        )
        _ = try fixture.makeDatabase()

        let session = try #require(
            try await provider.snapshot().sessions.first {
                $0.nativeID == "composer-1"
            }
        )
        #expect(session.state == .done)
        #expect(session.confidence == .observed)
    }

    @Test("does not apply terminal state from another generation")
    func hookGenerationMismatch() async throws {
        let fixture = try Fixture()
        let activity = CursorActivityStore()
        let databaseURL = try fixture.makeDatabase()
        try fixture.execute(
            """
            UPDATE cursorDiskKV
            SET value = '{"latestChatGenerationUUID":"generation-current"}'
            WHERE key = 'composerData:composer-1'
            """,
            at: databaseURL
        )
        let provider = CursorProvider(
            databaseURL: databaseURL,
            workspaceStorageURL: fixture.root.appendingPathComponent(
                "workspaceStorage"
            ),
            activityStore: activity,
            clock: { 200 }
        )
        _ = try await provider.recordHook(
            ProviderHookPayload(
                hookEventName: "stop",
                conversationID: "composer-1",
                generationID: "generation-old",
                status: "completed"
            ),
            observedAtMilliseconds: 150
        )

        let session = try #require(
            try await provider.snapshot().sessions.first {
                $0.nativeID == "composer-1"
            }
        )

        #expect(session.state != .done)
        #expect(session.confidence == .candidate)
    }

    @Test("missing Cursor data degrades without creating a database")
    func missingDatabase() async throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        let databaseURL = directory.appendingPathComponent("state.vscdb")
        let provider = CursorProvider(
            databaseURL: databaseURL,
            workspaceStorageURL: directory
        )

        await #expect(throws: CursorProviderError.self) {
            try await provider.snapshot()
        }
        #expect(!FileManager.default.fileExists(atPath: databaseURL.path))
    }

    @Test("commands require and preserve the exact selected target")
    func verifiedCommandDispatch() async throws {
        let fixture = try Fixture()
        let automation = FakeNativeAutomation(
            frontmostBundleID: CursorProvider.bundleID
        )
        let provider = CursorProvider(
            databaseURL: try fixture.makeDatabase(),
            workspaceStorageURL: fixture.root.appendingPathComponent(
                "workspaceStorage"
            ),
            activeSignalTTLMilliseconds: 1_000,
            activityStore: CursorActivityStore(),
            automation: automation,
            clock: { 1_000 }
        )

        let result = try await provider.executeCommand(
            nativeSessionID: "composer-1",
            commandID: .createPR
        )

        #expect(result.accepted)
        #expect(result.verdict == "DISPATCH_VERIFIED")
        #expect(
            await automation.dispatchedTexts() == [
                "Open a pull request for the current branch."
            ])
    }

    @Test("already-selected focus verifies without a shortcut")
    func selectedFocus() async throws {
        let fixture = try Fixture()
        let automation = FakeNativeAutomation(
            frontmostBundleID: CursorProvider.bundleID
        )
        let provider = CursorProvider(
            databaseURL: try fixture.makeDatabase(),
            workspaceStorageURL: fixture.root.appendingPathComponent(
                "workspaceStorage"
            ),
            activeSignalTTLMilliseconds: 1_000,
            activityStore: CursorActivityStore(),
            automation: automation,
            clock: { 1_000 }
        )

        let result = try await provider.focus(
            nativeSessionID: "composer-1"
        )

        #expect(result.accepted)
        #expect(result.verdict == "FOCUS_VERIFIED")
        #expect(await automation.shortcutCount() == 0)
    }

    @Test("empty project membership does not hide focus candidates")
    func emptyMembershipFocus() {
        #expect(
            CursorProvider.membershipIncludes(
                sessionID: "composer-1",
                memberships: [:]
            )
        )
        #expect(
            !CursorProvider.membershipIncludes(
                sessionID: "composer-1",
                memberships: ["composer-2": [:]]
            )
        )
    }
}

private struct Fixture {
    let root: URL
    let temporaryRoot: URL

    init() throws {
        let repositoryRoot = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
        root = repositoryRoot.appendingPathComponent(
            "contracts/providers/cursor/v1",
            isDirectory: true
        )
        temporaryRoot = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(
            at: temporaryRoot,
            withIntermediateDirectories: true
        )
    }

    func makeDatabase(sql: String? = nil) throws -> URL {
        let databaseURL = temporaryRoot.appendingPathComponent("state.vscdb")
        let script =
            try sql
            ?? String(
                contentsOf: root.appendingPathComponent("cursor-state.sql"),
                encoding: .utf8
            )
        var database: OpaquePointer?
        guard sqlite3_open(databaseURL.path, &database) == SQLITE_OK,
            let database
        else {
            throw FixtureError.sqlite("could not create fixture database")
        }
        defer { sqlite3_close(database) }
        var errorMessage: UnsafeMutablePointer<CChar>?
        guard
            sqlite3_exec(
                database,
                script,
                nil,
                nil,
                &errorMessage
            ) == SQLITE_OK
        else {
            let message =
                errorMessage.map { String(cString: $0) }
                ?? "unknown fixture error"
            sqlite3_free(errorMessage)
            throw FixtureError.sqlite(message)
        }
        return databaseURL
    }

    func execute(_ sql: String, at databaseURL: URL) throws {
        var database: OpaquePointer?
        guard sqlite3_open(databaseURL.path, &database) == SQLITE_OK,
            let database
        else {
            throw FixtureError.sqlite("could not update fixture database")
        }
        defer { sqlite3_close(database) }
        var errorMessage: UnsafeMutablePointer<CChar>?
        guard
            sqlite3_exec(
                database,
                sql,
                nil,
                nil,
                &errorMessage
            ) == SQLITE_OK
        else {
            let message =
                errorMessage.map { String(cString: $0) }
                ?? "unknown fixture update error"
            sqlite3_free(errorMessage)
            throw FixtureError.sqlite(message)
        }
    }
}

private enum FixtureError: Error {
    case sqlite(String)
}

private struct ExpectedInventory: Codable, Equatable {
    let providerID: String
    let readOnly: Bool
    let selectedSessionID: String?
    let sessions: [ExpectedSession]

    init(snapshot: ProviderSnapshot) {
        providerID = snapshot.providerID
        readOnly = snapshot.readOnly
        selectedSessionID = snapshot.selectedSessionID
        sessions = snapshot.sessions.map(ExpectedSession.init)
    }

    private enum CodingKeys: String, CodingKey {
        case providerID = "provider_id"
        case readOnly = "read_only"
        case selectedSessionID = "selected_session_id"
        case sessions
    }
}

private struct ExpectedSession: Codable, Equatable {
    let confidence: DeckConfidence
    let id: String
    let lastActivityAtMilliseconds: Int64
    let selected: Bool
    let state: SessionState
    let stateDetail: String
    let title: String
    let workspaceID: String
    let workspacePath: String?

    init(session: AgentSession) {
        confidence = session.confidence
        id = session.id
        lastActivityAtMilliseconds = session.lastActivityAtMilliseconds
        selected = session.selected
        state = session.state
        stateDetail = session.stateDetail
        title = session.title
        workspaceID = session.workspaceID
        workspacePath = session.workspacePath
    }

    private enum CodingKeys: String, CodingKey {
        case confidence
        case id
        case lastActivityAtMilliseconds = "last_activity_at_ms"
        case selected
        case state
        case stateDetail = "state_detail"
        case title
        case workspaceID = "workspace_id"
        case workspacePath = "workspace_path"
    }
}

private actor FakeNativeAutomation: NativeAutomating {
    private var currentBundleID: String?
    private var texts: [String] = []
    private var shortcuts = 0

    init(frontmostBundleID: String?) {
        currentBundleID = frontmostBundleID
    }

    func frontmostBundleID() async -> String? {
        currentBundleID
    }

    func activate(bundleID: String) async throws {
        currentBundleID = bundleID
    }

    func open(url: URL) async throws {}

    func postShortcut(
        keyCode: CGKeyCode,
        flags: CGEventFlags,
        bundleID: String
    ) async throws {
        shortcuts += 1
    }

    func postHeldModifierShortcut(
        modifierKeyCode: CGKeyCode,
        keyCode: CGKeyCode,
        flags: CGEventFlags,
        repeatCount: Int,
        bundleID: String
    ) async throws {
        shortcuts += repeatCount
    }

    func dispatchText(
        _ text: String,
        bundleID: String,
        inputMarker: String,
        focusKeyCode: CGKeyCode?,
        unfocusedPolicy: UnfocusedTextDispatchPolicy,
        submitCount: Int,
        targetVerifier: @escaping @Sendable () async throws -> Bool
    ) async throws -> ProviderActionResult {
        guard try await targetVerifier() else {
            throw ProviderOperationError.targetUnverified("fixture target")
        }
        texts.append(text)
        return ProviderActionResult(
            accepted: true,
            verdict: "DISPATCH_VERIFIED",
            details: ["executed": .boolean(true)]
        )
    }

    func dispatchCommandEnter(
        bundleID: String,
        inputMarker: String?,
        focusKeyCode: CGKeyCode?,
        targetVerifier: @escaping @Sendable () async throws -> Bool
    ) async throws -> ProviderActionResult {
        guard try await targetVerifier() else {
            throw ProviderOperationError.targetUnverified("fixture target")
        }
        return ProviderActionResult(
            accepted: true,
            verdict: "DISPATCH_VERIFIED",
            details: ["executed": .boolean(true)]
        )
    }

    func dispatchFrontmostText(
        _ text: String,
        submitKeyCode: CGKeyCode,
        submitFlags: CGEventFlags,
        submitCount: Int,
        bundleID: String
    ) async throws {}

    func dispatchedTexts() -> [String] {
        texts
    }

    func shortcutCount() -> Int {
        shortcuts
    }
}
