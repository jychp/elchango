import ElChangoCore
import Foundation
import Testing

@testable import ElChangoProviders

@Suite("Native Codex Desktop inventory")
struct CodexProviderTests {
    @Test("matches the shared Python inventory fixture")
    func sharedFixtureParity() async throws {
        let fixture = CodexSharedFixture()
        let provider = CodexProvider(
            sessionsRootURL: fixture.sessionsRoot,
            sessionIndexURL: fixture.sessionIndex,
            clock: { 1_000 }
        )

        let snapshot = try await provider.snapshot()
        let expected = try JSONDecoder().decode(
            CodexExpectedInventory.self,
            from: Data(contentsOf: fixture.expectedInventory)
        )

        #expect(CodexExpectedInventory(snapshot: snapshot) == expected)
        // Excludes CLI (codex-tui), subagents, and empty rollout artifacts.
        #expect(snapshot.sessions.count == 2)
        // No privileged capability is proven without a live experiment.
        #expect(snapshot.capabilities.isEmpty)
        #expect(snapshot.sessions.allSatisfy { $0.capabilities.isEmpty })
        #expect(snapshot.sessions.allSatisfy { $0.commands.isEmpty })
        #expect(snapshot.selectedNativeSessionID == nil)
        #expect(snapshot.readOnly)
        #expect(snapshot.observedAtMilliseconds == 1_000)
    }

    @Test("deduplicates a resumed thread to its newest rollout")
    func dedupKeepsNewestRollout() async throws {
        let fixture = CodexSharedFixture()
        let provider = CodexProvider(
            sessionsRootURL: fixture.sessionsRoot,
            sessionIndexURL: fixture.sessionIndex,
            clock: { 1_000 }
        )
        let snapshot = try await provider.snapshot()
        let alpha = try #require(
            snapshot.sessions.first {
                $0.nativeID == "11111111-1111-7111-8111-111111111111"
            }
        )
        // Persisted rollouts never derive a live state: without a hook signal
        // the session is idle. The newest rollout still sets last activity.
        #expect(alpha.state == .idle)
        #expect(alpha.confidence == .persisted)
        #expect(alpha.lastActivityAtMilliseconds == 1_783_617_000_000)
    }

    @Test("missing sessions root fails at provider level")
    func missingRootFails() async throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        let provider = CodexProvider(
            sessionsRootURL: root.appendingPathComponent("missing"),
            sessionIndexURL: root.appendingPathComponent("index.jsonl")
        )
        await #expect(throws: CodexProviderError.self) {
            _ = try await provider.snapshot()
        }
    }

    @Test("a confirmed Desktop record without cwd fails closed")
    func malformedDesktopRecordFails() async throws {
        let fixture = try CodexTemporaryFixture()
        try fixture.writeRollout(
            name: "rollout-2026-07-09T10-00-00-broken.jsonl",
            metaPayload: #"""
                {"id":"broken","originator":"Codex Desktop","thread_source":"user","timestamp":"2026-07-09T10:00:00.000Z"}
                """#,
            events: []
        )
        await #expect(throws: CodexProviderError.self) {
            _ = try await fixture.provider.snapshot()
        }
    }

    @Test("non-Desktop and empty rollouts are skipped, not fatal")
    func skipsUnrelatedRollouts() async throws {
        let fixture = try CodexTemporaryFixture()
        try fixture.writeRollout(
            name: "rollout-2026-07-09T10-00-00-cli.jsonl",
            metaPayload: #"""
                {"id":"cli","cwd":"/tmp/cli","originator":"codex-tui","thread_source":"user","timestamp":"2026-07-09T10:00:00.000Z"}
                """#,
            events: [("2026-07-09T10:00:05.000Z", "task_started")]
        )
        try fixture.writeRaw(
            name: "rollout-2026-07-09T10-01-00-empty.jsonl", contents: "")
        let snapshot = try await fixture.provider.snapshot()
        #expect(snapshot.sessions.isEmpty)
    }

    @Test("a fresh hook provides live state over the idle default")
    func hookOverridesTailState() async throws {
        let fixture = try CodexTemporaryFixture()
        let activity = CodexActivityStore()
        let provider = CodexProvider(
            sessionsRootURL: fixture.sessionsRoot,
            sessionIndexURL: fixture.sessionIndex,
            activityStore: activity,
            clock: { 150 }
        )
        try fixture.writeRollout(
            name: "rollout-2026-07-09T10-00-00-live.jsonl",
            metaPayload: #"""
                {"id":"live-thread","cwd":"/tmp/live","originator":"Codex Desktop","thread_source":"user","timestamp":"2026-07-09T10:00:00.000Z"}
                """#,
            events: [("2026-07-09T10:10:00.000Z", "task_complete")]
        )
        _ = try await provider.recordHook(
            ProviderHookPayload(
                hookEventName: "UserPromptSubmit",
                sessionID: "live-thread",
                cwd: "/tmp/live"
            ),
            observedAtMilliseconds: 100
        )
        let session = try #require(try await provider.snapshot().sessions.first)
        // Without a hook the session is idle; the fresh hook makes it working
        // at observed confidence.
        #expect(session.state == .working)
        #expect(session.confidence == .observed)
    }

    @Test("record cache invalidates when a rollout grows")
    func recordCacheInvalidation() async throws {
        let fixture = try CodexTemporaryFixture()
        let provider = fixture.provider
        try fixture.writeRollout(
            name: "rollout-2026-07-09T10-00-00-grow.jsonl",
            metaPayload: #"""
                {"id":"grow","cwd":"/tmp/grow","originator":"Codex Desktop","thread_source":"user","timestamp":"2026-07-09T10:00:00.000Z"}
                """#,
            events: [("2026-07-09T10:00:05.000Z", "task_started")]
        )
        #expect(
            try await provider.snapshot().sessions.first?
                .lastActivityAtMilliseconds == 1_783_591_205_000
        )

        try fixture.writeRollout(
            name: "rollout-2026-07-09T10-00-00-grow.jsonl",
            metaPayload: #"""
                {"id":"grow","cwd":"/tmp/grow","originator":"Codex Desktop","thread_source":"user","timestamp":"2026-07-09T10:00:00.000Z"}
                """#,
            events: [
                ("2026-07-09T10:00:05.000Z", "task_started"),
                ("2026-07-09T10:05:00.000Z", "task_complete"),
            ]
        )
        // The cache re-reads the grown rollout, so last activity advances to
        // the newer lifecycle event.
        #expect(
            try await provider.snapshot().sessions.first?
                .lastActivityAtMilliseconds == 1_783_591_500_000
        )
    }

    @Test("privileged actions fail closed pending live verification")
    func actionsFailClosed() async throws {
        let fixture = CodexSharedFixture()
        let provider = CodexProvider(
            sessionsRootURL: fixture.sessionsRoot,
            sessionIndexURL: fixture.sessionIndex
        )
        let native = "11111111-1111-7111-8111-111111111111"

        let focus = try await provider.focus(nativeSessionID: native)
        #expect(!focus.accepted)
        #expect(focus.verdict == "FOCUS_NOT_VERIFIED_REQUIRES_LIVE")

        let opened = try await provider.openNew()
        #expect(!opened.accepted)

        let command = try await provider.executeCommand(
            nativeSessionID: native,
            commandID: .accept
        )
        #expect(!command.accepted)
        #expect(command.verdict == "TARGET_NOT_SELECTED")
    }

    @Test("focus rejects an unknown target")
    func focusUnknownTarget() async throws {
        let fixture = CodexSharedFixture()
        let provider = CodexProvider(
            sessionsRootURL: fixture.sessionsRoot,
            sessionIndexURL: fixture.sessionIndex
        )
        await #expect(throws: ProviderOperationError.self) {
            _ = try await provider.focus(nativeSessionID: "does-not-exist")
        }
    }

    @Test("Codex hook state maps events to neutral states")
    func hookStateMapping() throws {
        let store = CodexActivityStore()
        func record(_ event: String, tool: String? = nil, at: Int64)
            -> SessionState
        {
            let observation = try? store.record(
                ProviderHookPayload(
                    hookEventName: event,
                    sessionID: "t",
                    cwd: "/tmp",
                    toolName: tool
                ),
                observedAtMilliseconds: at
            )
            return observation?.state ?? .unknown
        }
        #expect(record("UserPromptSubmit", at: 1) == .working)
        #expect(record("PermissionRequest", tool: "shell", at: 2) == .waiting)
        #expect(record("PostToolUse", at: 3) == .working)
        #expect(record("Stop", at: 4) == .done)
        #expect(record("SessionEnd", at: 5) == .idle)
    }

    @Test("stale working hook degrades to unknown")
    func staleHookDegrades() throws {
        let store = CodexActivityStore(terminalDeadlineMilliseconds: 1_000)
        _ = try store.record(
            ProviderHookPayload(
                hookEventName: "UserPromptSubmit",
                sessionID: "t",
                cwd: "/tmp"
            ),
            observedAtMilliseconds: 0
        )
        let stale = store.state(for: "t", observedAtMilliseconds: 2_000)
        #expect(stale?.0 == .unknown)
    }
}

private struct CodexSharedFixture {
    let root: URL

    init() {
        let repositoryRoot = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
        root = repositoryRoot.appendingPathComponent(
            "contracts/providers/codex/v1",
            isDirectory: true
        )
    }

    var sessionsRoot: URL {
        root.appendingPathComponent("sessions", isDirectory: true)
    }

    var sessionIndex: URL {
        root.appendingPathComponent("session_index.jsonl")
    }

    var expectedInventory: URL {
        root.appendingPathComponent("expected-inventory.json")
    }
}

private struct CodexTemporaryFixture {
    let root: URL
    let sessionsRoot: URL
    let dayRoot: URL
    let sessionIndex: URL

    init() throws {
        root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        sessionsRoot = root.appendingPathComponent("sessions", isDirectory: true)
        dayRoot =
            sessionsRoot
            .appendingPathComponent("2026", isDirectory: true)
            .appendingPathComponent("07", isDirectory: true)
            .appendingPathComponent("09", isDirectory: true)
        sessionIndex = root.appendingPathComponent("session_index.jsonl")
        try FileManager.default.createDirectory(
            at: dayRoot,
            withIntermediateDirectories: true
        )
    }

    var provider: CodexProvider {
        CodexProvider(
            sessionsRootURL: sessionsRoot,
            sessionIndexURL: sessionIndex,
            clock: { 1_000 }
        )
    }

    func writeRollout(
        name: String,
        metaPayload: String,
        events: [(String, String)]
    ) throws {
        var lines = [
            #"{"type":"session_meta","payload":\#(metaPayload)}"#
        ]
        for (timestamp, type) in events {
            lines.append(
                #"{"timestamp":"\#(timestamp)","type":"event_msg","payload":{"type":"\#(type)"}}"#
            )
        }
        try writeRaw(name: name, contents: lines.joined(separator: "\n") + "\n")
    }

    func writeRaw(name: String, contents: String) throws {
        try contents.write(
            to: dayRoot.appendingPathComponent(name),
            atomically: true,
            encoding: .utf8
        )
    }
}

private struct CodexExpectedInventory: Codable, Equatable {
    let providerID: String
    let readOnly: Bool
    let selectedSessionID: String?
    let sessions: [CodexExpectedSession]

    init(snapshot: ProviderSnapshot) {
        providerID = snapshot.providerID
        readOnly = snapshot.readOnly
        selectedSessionID = snapshot.selectedSessionID
        sessions = snapshot.sessions.map(CodexExpectedSession.init)
    }

    private enum CodingKeys: String, CodingKey {
        case providerID = "provider_id"
        case readOnly = "read_only"
        case selectedSessionID = "selected_session_id"
        case sessions
    }
}

private struct CodexExpectedSession: Codable, Equatable {
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
