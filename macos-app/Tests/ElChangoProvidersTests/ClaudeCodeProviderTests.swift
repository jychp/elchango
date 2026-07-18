@preconcurrency import ApplicationServices
import ElChangoCore
@testable import ElChangoProviders
import Foundation
import Testing

@Suite("Native Claude Desktop inventory")
struct ClaudeCodeProviderTests {
    @Test("matches the shared Python inventory fixture")
    func sharedFixtureParity() async throws {
        let fixture = ClaudeSharedFixture()
        let provider = ClaudeCodeProvider(
            desktopSessionsRootURL: fixture.desktopRoot,
            projectsRootURL: fixture.projectsRoot,
            desktopConfigURL: fixture.desktopConfig,
            clock: { 1_000 }
        )

        let snapshot = try await provider.snapshot()
        let expected = try JSONDecoder().decode(
            ClaudeExpectedInventory.self,
            from: Data(contentsOf: fixture.expectedInventory)
        )

        #expect(ClaudeExpectedInventory(snapshot: snapshot) == expected)
        #expect(snapshot.capabilities == [
            .focusSession, .newSession, .executeCommand,
        ])
        #expect(
            snapshot.sessions.allSatisfy {
                $0.capabilities.contains(.executeCommand)
            }
        )
        #expect(
            snapshot.sessions.allSatisfy {
                $0.commands == ClaudeCodeProvider.commands
            }
        )
        #expect(snapshot.observedAtMilliseconds == 1_000)
    }

    @Test("ordered JSON preserves persisted Claude group order")
    func orderedGroupConfiguration() throws {
        let value = try OrderedJSON(
            data: Data(
                """
                {"customGroupOrder":{"second":["code:b"],"first":["code:a"]}}
                """.utf8
            )
        )

        #expect(
            value["customGroupOrder"]?.objectEntries?.map(\.0)
                == ["second", "first"]
        )
    }

    @Test("requires both persistent inventory roots")
    func missingRootsFailAtProviderLevel() async throws {
        let fixture = try ClaudeTemporaryFixture()
        let missingDesktop = fixture.root.appendingPathComponent("missing")
        let missingProjects = fixture.root.appendingPathComponent(
            "missing-projects"
        )

        await #expect(throws: ClaudeCodeProviderError.self) {
            try await ClaudeCodeProvider(
                desktopSessionsRootURL: missingDesktop,
                projectsRootURL: fixture.projectsRoot
            ).snapshot()
        }
        await #expect(throws: ClaudeCodeProviderError.self) {
            try await ClaudeCodeProvider(
                desktopSessionsRootURL: fixture.desktopRoot,
                projectsRootURL: missingProjects
            ).snapshot()
        }
    }

    @Test("rejects missing and mistyped required metadata")
    func strictRequiredMetadataValidation() async throws {
        let malformedRecords = [
            """
            {"sessionId":"local_bad","isArchived":false}
            """,
            """
            {
              "sessionId":"local_bad","cliSessionId":"cli-bad",
              "cwd":"","originCwd":"/tmp/repository","createdAt":1,
              "lastActivityAt":2,"isArchived":false
            }
            """,
            """
            {
              "sessionId":"local_bad","cliSessionId":"cli-bad",
              "cwd":"/tmp/worktree","originCwd":"/tmp/repository",
              "createdAt":true,"lastActivityAt":2,"isArchived":false
            }
            """,
            """
            {
              "sessionId":"local_bad","cliSessionId":"cli-bad",
              "cwd":"/tmp/worktree","originCwd":"/tmp/repository",
              "createdAt":1.0,"lastActivityAt":2,"isArchived":false
            }
            """,
            """
            {
              "sessionId":"local_bad","cliSessionId":"cli-bad",
              "cwd":"/tmp/worktree","originCwd":"/tmp/repository",
              "createdAt":1,"lastActivityAt":-1,"isArchived":false
            }
            """,
            """
            {
              "sessionId":"local_bad","cliSessionId":"cli-bad",
              "cwd":"/tmp/worktree","originCwd":"/tmp/repository",
              "createdAt":1,"lastActivityAt":2,"isArchived":"false"
            }
            """,
            """
            {
              "sessionId":"local_bad","cliSessionId":"cli-bad",
              "cwd":"/tmp/worktree","originCwd":"/tmp/repository",
              "createdAt":1,"lastActivityAt":2,"isArchived":1
            }
            """,
        ]

        for record in malformedRecords {
            let fixture = try ClaudeTemporaryFixture()
            try fixture.writeRawRecord(record, named: "local_bad.json")
            await #expect(throws: ClaudeCodeProviderError.self) {
                try await fixture.provider.snapshot()
            }
        }
    }

    @Test("validates optional metadata and filename identity")
    func strictOptionalMetadataAndIdentityValidation() async throws {
        let invalidTitle = try ClaudeTemporaryFixture()
        try invalidTitle.writeRecord(
            desktopID: "local_bad",
            cliID: "cli-bad",
            extraMetadata: #""title":42"#
        )
        await #expect(throws: ClaudeCodeProviderError.self) {
            try await invalidTitle.provider.snapshot()
        }

        let invalidFocus = try ClaudeTemporaryFixture()
        try invalidFocus.writeRecord(
            desktopID: "local_bad",
            cliID: "cli-bad",
            extraMetadata: #""lastFocusedAt":false"#
        )
        await #expect(throws: ClaudeCodeProviderError.self) {
            try await invalidFocus.provider.snapshot()
        }

        let mismatchedFilename = try ClaudeTemporaryFixture()
        try mismatchedFilename.writeRecord(
            desktopID: "local_actual",
            cliID: "cli-bad",
            filename: "local_different.json"
        )
        await #expect(throws: ClaudeCodeProviderError.self) {
            try await mismatchedFilename.provider.snapshot()
        }
    }

    @Test("fails the whole provider for every malformed record")
    func malformedArchivedRecordFailsProvider() async throws {
        let fixture = try ClaudeTemporaryFixture()
        try fixture.writeRecord(
            desktopID: "local_valid",
            cliID: "cli-valid"
        )
        try fixture.writeTranscript(cliID: "cli-valid")
        try fixture.writeRawRecord(
            """
            {
              "sessionId":"local_archived",
              "cliSessionId":"cli-archived",
              "cwd":"/tmp/archive","originCwd":"/tmp/repository",
              "createdAt":1,"lastActivityAt":"bad","isArchived":true
            }
            """,
            named: "local_archived.json"
        )

        await #expect(throws: ClaudeCodeProviderError.self) {
            try await fixture.provider.snapshot()
        }
    }

    @Test("requires one transcript for each visible Desktop record")
    func transcriptMappingMustBeUnique() async throws {
        let missing = try ClaudeTemporaryFixture()
        try missing.writeRecord(
            desktopID: "local_missing",
            cliID: "cli-missing"
        )
        await #expect(throws: ClaudeCodeProviderError.self) {
            try await missing.provider.snapshot()
        }

        let duplicate = try ClaudeTemporaryFixture()
        try duplicate.writeRecord(
            desktopID: "local_duplicate",
            cliID: "cli-duplicate"
        )
        try duplicate.writeTranscript(
            cliID: "cli-duplicate",
            project: "one"
        )
        try duplicate.writeTranscript(
            cliID: "cli-duplicate",
            project: "two"
        )
        await #expect(throws: ClaudeCodeProviderError.self) {
            try await duplicate.provider.snapshot()
        }
    }

    @Test("ignores transcript mapping for archived records")
    func archivedRecordsDoNotRequireTranscripts() async throws {
        let fixture = try ClaudeTemporaryFixture()
        try fixture.writeRecord(
            desktopID: "local_archived",
            cliID: "cli-archived",
            archived: true
        )

        let snapshot = try await fixture.provider.snapshot()

        #expect(snapshot.sessions.isEmpty)
        #expect(snapshot.selectedNativeSessionID == nil)
    }

    @Test("selects only a uniquely newest focus timestamp")
    func newestFocusMustBeUnique() async throws {
        let fixture = try ClaudeTemporaryFixture()
        for id in ["a", "b"] {
            try fixture.writeRecord(
                desktopID: "local_\(id)",
                cliID: "cli-\(id)",
                lastActivityAt: id == "a" ? 200 : 100,
                lastFocusedAt: 500
            )
            try fixture.writeTranscript(cliID: "cli-\(id)")
        }

        let snapshot = try await fixture.provider.snapshot()

        #expect(snapshot.selectedNativeSessionID == nil)
        #expect(snapshot.selectedSessionID == nil)
        #expect(snapshot.sessions.allSatisfy { !$0.selected })
    }

    @Test("sorts by activity then provider-qualified session ID")
    func deterministicSorting() async throws {
        let fixture = try ClaudeTemporaryFixture()
        for id in ["z", "a"] {
            try fixture.writeRecord(
                desktopID: "local_\(id)",
                cliID: "cli-\(id)",
                lastActivityAt: 100
            )
            try fixture.writeTranscript(cliID: "cli-\(id)")
        }

        let snapshot = try await fixture.provider.snapshot()

        #expect(
            snapshot.sessions.map(\.id)
                == ["claude-code:local_a", "claude-code:local_z"]
        )
    }

    @Test("reads metadata without parsing large message content")
    func boundedMetadataPrefix() async throws {
        let fixture = try ClaudeTemporaryFixture()
        let messages = String(repeating: "x", count: 80_000)
        try fixture.writeRawRecord(
            """
            {
              "sessionId":"local_large","cliSessionId":"cli-large",
              "cwd":"/tmp/large","originCwd":"/tmp/repository",
              "createdAt":1,"lastActivityAt":2,"isArchived":false,
              "title":"Large","lastFocusedAt":3,
              "messages":[{"content":"\(messages)"}]
            }
            """,
            named: "local_large.json"
        )
        try fixture.writeTranscript(cliID: "cli-large")

        let snapshot = try await fixture.provider.snapshot()

        #expect(snapshot.sessions.map(\.nativeID) == ["local_large"])
    }

    @Test("verified commands preserve the selected Claude target")
    func verifiedCommand() async throws {
        let fixture = try ClaudeTemporaryFixture()
        try fixture.writeRecord(
            desktopID: "local_target",
            cliID: "cli-target",
            lastFocusedAt: 500
        )
        try fixture.writeTranscript(cliID: "cli-target")
        let config = try fixture.writeShortcutConfig(
            starred: ["local_target"]
        )
        let automation = ClaudeAutomation(
            frontmostBundleID: ClaudeCodeProvider.bundleID
        )
        let provider = ClaudeCodeProvider(
            desktopSessionsRootURL: fixture.desktopRoot,
            projectsRootURL: fixture.projectsRoot,
            desktopConfigURL: config,
            automation: automation,
            clock: { 1_000 }
        )

        let result = try await provider.executeCommand(
            nativeSessionID: "local_target",
            commandID: .createPR
        )

        #expect(result.accepted)
        #expect(await automation.dispatchedTexts() == [
            "Open a pull request for the current branch.",
        ])
    }

    @Test("Claude hooks overlay only known persistent sessions")
    func hookOverlay() async throws {
        let fixture = try ClaudeTemporaryFixture()
        try fixture.writeRecord(
            desktopID: "local_target",
            cliID: "cli-target"
        )
        try fixture.writeTranscript(cliID: "cli-target")
        let activity = ClaudeActivityStore(
            terminalDeadlineMilliseconds: 1_000,
            ttlMilliseconds: 2_000
        )
        let provider = ClaudeCodeProvider(
            desktopSessionsRootURL: fixture.desktopRoot,
            projectsRootURL: fixture.projectsRoot,
            activityStore: activity,
            clock: { 150 }
        )
        _ = try await provider.recordHook(
            ProviderHookPayload(
                hookEventName: "UserPromptSubmit",
                sessionID: "cli-target",
                cwd: "/tmp/worktree-local_target",
                transcriptPath: "/tmp/cli-target.jsonl"
            ),
            observedAtMilliseconds: 100
        )

        let session = try #require(
            try await provider.snapshot().sessions.first
        )

        #expect(session.state == .working)
        #expect(session.confidence == .observed)
    }

    @Test("record cache invalidates on metadata replacement")
    func recordCacheInvalidation() async throws {
        let fixture = try ClaudeTemporaryFixture()
        try fixture.writeRecord(
            desktopID: "local_target",
            cliID: "cli-target",
            extraMetadata: #""title":"Before""#
        )
        try fixture.writeTranscript(cliID: "cli-target")
        let provider = fixture.provider
        #expect(
            try await provider.snapshot().sessions.first?.title == "Before"
        )

        try fixture.writeRecord(
            desktopID: "local_target",
            cliID: "cli-target",
            lastActivityAt: 300,
            extraMetadata: #""title":"After""#
        )

        let updated = try await provider.snapshot().sessions.first
        #expect(updated?.title == "After")
        #expect(updated?.lastActivityAtMilliseconds == 300)
    }
}

private struct ClaudeSharedFixture {
    let root: URL

    init() {
        let repositoryRoot = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
        root = repositoryRoot.appendingPathComponent(
            "contracts/providers/claude-code/v1",
            isDirectory: true
        )
    }

    var desktopRoot: URL {
        root.appendingPathComponent("desktop", isDirectory: true)
    }

    var projectsRoot: URL {
        root.appendingPathComponent("projects", isDirectory: true)
    }

    var desktopConfig: URL {
        root.appendingPathComponent("claude_desktop_config.json")
    }

    var expectedInventory: URL {
        root.appendingPathComponent("expected-inventory.json")
    }
}

private struct ClaudeTemporaryFixture {
    let root: URL
    let desktopRoot: URL
    let projectsRoot: URL
    let recordsRoot: URL

    init() throws {
        root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        desktopRoot = root.appendingPathComponent("desktop", isDirectory: true)
        projectsRoot = root.appendingPathComponent(
            "projects",
            isDirectory: true
        )
        recordsRoot = desktopRoot
            .appendingPathComponent("account", isDirectory: true)
            .appendingPathComponent("workspace", isDirectory: true)
        try FileManager.default.createDirectory(
            at: recordsRoot,
            withIntermediateDirectories: true
        )
        try FileManager.default.createDirectory(
            at: projectsRoot,
            withIntermediateDirectories: true
        )
    }

    var provider: ClaudeCodeProvider {
        ClaudeCodeProvider(
            desktopSessionsRootURL: desktopRoot,
            projectsRootURL: projectsRoot,
            clock: { 1_000 }
        )
    }

    func writeRecord(
        desktopID: String,
        cliID: String,
        filename: String? = nil,
        lastActivityAt: Int64 = 200,
        lastFocusedAt: Int64? = nil,
        archived: Bool = false,
        extraMetadata: String? = nil
    ) throws {
        let optionalFields = [
            lastFocusedAt.map { #""lastFocusedAt":\#($0)"# },
            extraMetadata,
        ].compactMap { $0 }.joined(separator: ",")
        let optionalSuffix = optionalFields.isEmpty
            ? ""
            : ",\(optionalFields)"
        try writeRawRecord(
            """
            {
              "sessionId":"\(desktopID)","cliSessionId":"\(cliID)",
              "cwd":"/tmp/worktree-\(desktopID)",
              "originCwd":"/tmp/repository","createdAt":100,
              "lastActivityAt":\(lastActivityAt),
              "isArchived":\(archived)\(optionalSuffix),
              "messages":[]
            }
            """,
            named: filename ?? "\(desktopID).json"
        )
    }

    func writeRawRecord(_ value: String, named filename: String) throws {
        try value.write(
            to: recordsRoot.appendingPathComponent(filename),
            atomically: true,
            encoding: .utf8
        )
    }

    func writeTranscript(
        cliID: String,
        project: String = "project"
    ) throws {
        let directory = projectsRoot.appendingPathComponent(
            project,
            isDirectory: true
        )
        try FileManager.default.createDirectory(
            at: directory,
            withIntermediateDirectories: true
        )
        try "{}\n".write(
            to: directory.appendingPathComponent("\(cliID).jsonl"),
            atomically: true,
            encoding: .utf8
        )
    }

    func writeShortcutConfig(starred: [String]) throws -> URL {
        let url = root.appendingPathComponent("claude_desktop_config.json")
        let data = try JSONSerialization.data(
            withJSONObject: [
                "preferences": [
                    "epitaxyPrefs": [
                        "starred-local-code-sessions": starred,
                        "dframe-local-slice": [
                            "customGroupAssignments": [:],
                            "customGroupOrder": [:],
                        ],
                    ],
                ],
            ],
            options: [.sortedKeys]
        )
        try data.write(to: url)
        return url
    }
}

private struct ClaudeExpectedInventory: Codable, Equatable {
    let providerID: String
    let readOnly: Bool
    let selectedSessionID: String?
    let sessions: [ClaudeExpectedSession]

    init(snapshot: ProviderSnapshot) {
        providerID = snapshot.providerID
        readOnly = snapshot.readOnly
        selectedSessionID = snapshot.selectedSessionID
        sessions = snapshot.sessions.map(ClaudeExpectedSession.init)
    }

    private enum CodingKeys: String, CodingKey {
        case providerID = "provider_id"
        case readOnly = "read_only"
        case selectedSessionID = "selected_session_id"
        case sessions
    }
}

private struct ClaudeExpectedSession: Codable, Equatable {
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

private actor ClaudeAutomation: NativeAutomating {
    private var bundleID: String?
    private var texts: [String] = []

    init(frontmostBundleID: String?) {
        bundleID = frontmostBundleID
    }

    func frontmostBundleID() async -> String? {
        bundleID
    }

    func activate(bundleID: String) async throws {
        self.bundleID = bundleID
    }

    func open(url: URL) async throws {}

    func postShortcut(
        keyCode: CGKeyCode,
        flags: CGEventFlags,
        bundleID: String
    ) async throws {}

    func dispatchText(
        _ text: String,
        bundleID: String,
        inputMarker: String,
        focusKeyCode: CGKeyCode?,
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

    func dispatchedTexts() -> [String] {
        texts
    }
}
