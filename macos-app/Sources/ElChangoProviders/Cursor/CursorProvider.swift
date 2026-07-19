@preconcurrency import ApplicationServices
import ElChangoCore
import Foundation

public enum CursorProviderError: LocalizedError {
    case databaseNotFound(URL)
    case unsupportedSchema([String])
    case unsupportedTableSchema(table: String, missingColumns: [String])
    case readFailed(String)

    public var errorDescription: String? {
        switch self {
        case .databaseNotFound(let url):
            "Cursor database not found: \(url.path)"
        case .unsupportedSchema(let tables):
            "Unsupported Cursor schema; missing tables: \(tables)"
        case .unsupportedTableSchema(let table, let columns):
            "Unsupported \(table) schema; missing columns: \(columns)"
        case .readFailed(let message):
            "Cursor database read failed: \(message)"
        }
    }
}

public actor CursorProvider: AgentProvider {
    public nonisolated let descriptor = ProviderDescriptor(
        id: "cursor",
        displayName: "Cursor",
        icon: .cursor,
        capabilities: [.focusSession, .newSession, .executeCommand]
    )

    public static let activeSignalTTLMilliseconds: Int64 = 5 * 60 * 1_000
    public static let bundleID = "com.todesktop.230313mzl4w4u92"
    public static let inputMarker =
        "tiptapProseMirrorui-prompt-input-editor__inputProseMirror-focused"
    public static let emptyInputPlaceholder = "Send follow-up\n"
    public static let commands: Set<CommandID> = [
        .accept, .createPR, .commitPush, .compact,
    ]

    private let databaseURL: URL
    private let workspaceStorageURL: URL
    private let activeSignalTTLMilliseconds: Int64
    private let clock: @Sendable () -> Int64
    private let activityStore: CursorActivityStore
    private let automation: any NativeAutomating
    private let actionGate: PrivilegedActionGate

    public init(
        databaseURL: URL = CursorProvider.defaultDatabaseURL,
        workspaceStorageURL: URL =
            CursorProvider.defaultWorkspaceStorageURL,
        activeSignalTTLMilliseconds: Int64 =
            CursorProvider.activeSignalTTLMilliseconds,
        activityStore: CursorActivityStore = CursorActivityStore(),
        automation: any NativeAutomating = NativeAutomation(),
        actionGate: PrivilegedActionGate = PrivilegedActionGate(),
        clock: @escaping @Sendable () -> Int64 = {
            Int64(Date().timeIntervalSince1970 * 1_000)
        }
    ) {
        self.databaseURL = databaseURL
        self.workspaceStorageURL = workspaceStorageURL
        self.activeSignalTTLMilliseconds = activeSignalTTLMilliseconds
        self.activityStore = activityStore
        self.automation = automation
        self.actionGate = actionGate
        self.clock = clock
    }

    public func snapshot() async throws -> ProviderSnapshot {
        guard FileManager.default.fileExists(atPath: databaseURL.path) else {
            throw CursorProviderError.databaseNotFound(databaseURL)
        }

        let observedAtMilliseconds = clock()
        do {
            let connection = try SQLiteReadConnection(url: databaseURL)
            try validateSchema(connection)
            let rawSelectedID = try readSelectedID(connection)
            let workspacePaths = loadWorkspacePaths()
            let memberships = try readItemObject(
                connection,
                key: "glass.localAgentProjectMembership.v1"
            )
            let candidates = try readSessions(
                connection,
                workspacePaths: workspacePaths,
                memberships: memberships,
                observedAtMilliseconds: observedAtMilliseconds
            )
            let selectedID = Self.exactSelectedID(
                rawSelectedID,
                sessions: candidates.map(\.session)
            )
            activityStore.observeSelection(
                selectedID,
                observedAtMilliseconds: observedAtMilliseconds
            )
            let sessions = candidates.map { candidate in
                var state = (
                    candidate.session.state,
                    candidate.session.confidence,
                    candidate.session.stateDetail
                )
                if let hookState = activityStore.state(
                    for: candidate.session.nativeID,
                    observedAtMilliseconds: observedAtMilliseconds,
                    currentGenerationID: candidate.generationID
                ),
                    hookState.0 == .done
                        || hookState.0 == .error
                        || candidate.session.state != .waiting
                {
                    state = hookState
                }
                return Self.session(
                    candidate.session,
                    selected: candidate.session.nativeID == selectedID,
                    state: state
                )
            }
            try connection.commit()
            return ProviderSnapshot(
                providerID: descriptor.id,
                capabilities: descriptor.capabilities,
                observedAtMilliseconds: observedAtMilliseconds,
                selectedNativeSessionID: selectedID,
                sessions: sessions,
                source: databaseURL.path,
                readOnly: true
            )
        } catch let error as CursorProviderError {
            throw error
        } catch {
            throw CursorProviderError.readFailed(error.localizedDescription)
        }
    }

    public func isFrontmost() async throws -> Bool {
        await automation.frontmostBundleID() == Self.bundleID
    }

    public static var defaultCursorRootURL: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(
                "Library/Application Support/Cursor/User",
                isDirectory: true
            )
    }

    public static var defaultDatabaseURL: URL {
        defaultCursorRootURL
            .appendingPathComponent("globalStorage", isDirectory: true)
            .appendingPathComponent("state.vscdb")
    }

    public static var defaultWorkspaceStorageURL: URL {
        defaultCursorRootURL.appendingPathComponent(
            "workspaceStorage",
            isDirectory: true
        )
    }

    private func validateSchema(
        _ connection: SQLiteReadConnection
    ) throws {
        let tables = Set(
            try connection.withRows(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ) { row in
                try row.text(0)
            }
        )
        let required = Set(["ItemTable", "composerHeaders", "cursorDiskKV"])
        let missing = required.subtracting(tables).sorted()
        guard missing.isEmpty else {
            throw CursorProviderError.unsupportedSchema(missing)
        }
        let requiredColumns: [String: Set<String>] = [
            "ItemTable": ["key", "value"],
            "composerHeaders": [
                "composerId",
                "workspaceId",
                "lastUpdatedAt",
                "isArchived",
                "isSubagent",
                "value",
            ],
            "cursorDiskKV": ["key", "value"],
        ]
        for table in requiredColumns.keys.sorted() {
            let columns = Set(
                try connection.withRows(
                    "PRAGMA table_info(\"\(table)\")"
                ) { row in
                    try row.text(1)
                }
            )
            let missingColumns = requiredColumns[table, default: []]
                .subtracting(columns)
                .sorted()
            guard missingColumns.isEmpty else {
                throw CursorProviderError.unsupportedTableSchema(
                    table: table,
                    missingColumns: missingColumns
                )
            }
        }
    }

    private func readSelectedID(
        _ connection: SQLiteReadConnection
    ) throws -> String? {
        let values: [String] = try connection.withRows(
            "SELECT value FROM ItemTable WHERE key = ?",
            bindings: ["cursor/glass.selectedAgent"]
        ) { row in
            try row.text(0, allowBlob: true)
        }
        guard let value = values.first else { return nil }
        let selected = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return selected.isEmpty ? nil : selected
    }

    private func readSessions(
        _ connection: SQLiteReadConnection,
        workspacePaths: [String: String],
        memberships: JSONObject,
        observedAtMilliseconds: Int64
    ) throws -> [CursorCandidate] {
        let rows: [CursorSessionRow] = try connection.withRows(
            """
            SELECT composerId, workspaceId, lastUpdatedAt,
                   isArchived, isSubagent, value
            FROM composerHeaders
            """
        ) { row in
            guard let composerID = try row.text(0) else { return nil }
            return CursorSessionRow(
                composerID: composerID,
                workspaceID: try row.text(1) ?? "",
                lastUpdatedAtMilliseconds: row.integer(2),
                isArchived: try row.boolean(3),
                isSubagent: try row.boolean(4),
                header: Self.parseObject(
                    try row.text(5, allowBlob: true)
                )
            )
        }

        var sessions: [CursorCandidate] = []
        for row in rows {
            if !memberships.isEmpty && memberships[row.composerID] == nil {
                continue
            }
            if row.isArchived
                || row.isSubagent
                || Self.bool(row.header["isDraft"])
                || Self.bool(row.header["isEphemeral"])
            {
                continue
            }
            guard
                let lastActivityAtMilliseconds =
                    row.lastUpdatedAtMilliseconds
            else {
                continue
            }

            let data = try readDiskObject(
                connection,
                key: "composerData:\(row.composerID)"
            )
            let inferred = try inferState(
                connection,
                composerID: row.composerID,
                header: row.header,
                data: data,
                lastActivityAtMilliseconds: lastActivityAtMilliseconds,
                observedAtMilliseconds: observedAtMilliseconds
            )
            let generationID = Self.string(
                data["latestChatGenerationUUID"]
                    ?? data["chatGenerationUUID"]
            )
            sessions.append(
                CursorCandidate(
                    session: AgentSession(
                        providerID: descriptor.id,
                        nativeID: row.composerID,
                        capabilities: descriptor.capabilities,
                        icon: descriptor.icon,
                        title: Self.string(row.header["name"])
                            ?? "Untitled session",
                        workspaceID: row.workspaceID,
                        workspacePath: Self.embeddedWorkspacePath(row.header)
                            ?? workspacePaths[row.workspaceID],
                        state: inferred.state,
                        confidence: inferred.confidence,
                        stateDetail: inferred.detail,
                        selected: false,
                        lastActivityAtMilliseconds: lastActivityAtMilliseconds,
                        commands: Self.commands
                    ),
                    generationID: generationID
                )
            )
        }
        return sessions.sorted {
            if $0.session.lastActivityAtMilliseconds
                != $1.session.lastActivityAtMilliseconds
            {
                return $0.session.lastActivityAtMilliseconds
                    > $1.session.lastActivityAtMilliseconds
            }
            return $0.session.id < $1.session.id
        }
    }

    private func inferState(
        _ connection: SQLiteReadConnection,
        composerID: String,
        header: JSONObject,
        data: JSONObject,
        lastActivityAtMilliseconds: Int64,
        observedAtMilliseconds: Int64
    ) throws -> InferredState {
        guard !data.isEmpty else {
            return InferredState(
                state: .idle,
                confidence: .unknown,
                detail: "composer data unavailable"
            )
        }

        let latestTool = try latestToolStatus(
            connection,
            composerID: composerID,
            data: data
        )
        let tool = latestTool.tool?.lowercased()
        let result = latestTool.result?.lowercased()
        let activeSignalIsFresh =
            observedAtMilliseconds - lastActivityAtMilliseconds
            <= activeSignalTTLMilliseconds
        let blocking =
            Self.bool(data["hasBlockingPendingActions"])
            || Self.bool(header["hasBlockingPendingActions"])
            || Self.bool(data["hasPendingPlan"])
            || Self.bool(header["hasPendingPlan"])

        if blocking {
            if activeSignalIsFresh {
                return .init(
                    state: .waiting,
                    confidence: .candidate,
                    detail: "user action or plan pending"
                )
            }
            return .init(
                state: .idle,
                confidence: .persisted,
                detail: "stale blocking signal ignored"
            )
        }

        let failures = Set([
            "error", "failed", "failure", "aborted", "cancelled", "canceled",
        ])
        if result.map(failures.contains) == true
            || tool.map(Set(["error", "failed", "failure"]).contains) == true
        {
            if activeSignalIsFresh {
                return .init(
                    state: .working,
                    confidence: .candidate,
                    detail: "recent provisional tool result; turn may continue"
                )
            }
            return .init(
                state: .idle,
                confidence: .persisted,
                detail: "stale tool result ignored"
            )
        }

        if let tool,
            Set(["loading", "running", "pending", "in_progress"])
                .contains(tool)
        {
            if activeSignalIsFresh {
                return .init(
                    state: .working,
                    confidence: .candidate,
                    detail: "tool \(tool)"
                )
            }
            return .init(
                state: .idle,
                confidence: .persisted,
                detail: "stale tool \(tool) signal ignored"
            )
        }

        if Self.nonEmptyArray(data["generatingBubbleIds"])
            || Self.bool(data["isContinuationInProgress"])
        {
            if activeSignalIsFresh {
                return .init(
                    state: .working,
                    confidence: .candidate,
                    detail: "generation signal present"
                )
            }
            return .init(
                state: .idle,
                confidence: .persisted,
                detail: "stale generation signal ignored"
            )
        }

        if tool == "completed" {
            if activeSignalIsFresh {
                return .init(
                    state: .working,
                    confidence: .candidate,
                    detail: "recent tool completion; awaiting terminal signal"
                )
            }
            return .init(
                state: .idle,
                confidence: .persisted,
                detail: result == "success"
                    ? "last tool completed successfully"
                    : "last tool completed"
            )
        }

        let rawStatus = Self.string(data["status"])
        if let rawStatus, Set(["error", "failed"]).contains(rawStatus) {
            if activeSignalIsFresh {
                return .init(
                    state: .waiting,
                    confidence: .candidate,
                    detail: "composer \(rawStatus)"
                )
            }
            return .init(
                state: .idle,
                confidence: .persisted,
                detail: "stale composer \(rawStatus)"
            )
        }
        if rawStatus == "completed" {
            if activeSignalIsFresh {
                return .init(
                    state: .working,
                    confidence: .candidate,
                    detail: "recent completion; awaiting terminal signal"
                )
            }
            return .init(
                state: .idle,
                confidence: .persisted,
                detail: "last turn completed"
            )
        }
        if rawStatus == "aborted" {
            if activeSignalIsFresh {
                return .init(
                    state: .working,
                    confidence: .candidate,
                    detail: "recent activity with stale aborted status"
                )
            }
            return .init(
                state: .idle,
                confidence: .persisted,
                detail: "last turn aborted"
            )
        }
        if let rawStatus,
            Set(["generating", "running", "pending"]).contains(rawStatus)
        {
            if activeSignalIsFresh {
                return .init(
                    state: .working,
                    confidence: .candidate,
                    detail: "composer \(rawStatus)"
                )
            }
            return .init(
                state: .idle,
                confidence: .persisted,
                detail: "stale composer \(rawStatus) ignored"
            )
        }
        if activeSignalIsFresh {
            return .init(
                state: .working,
                confidence: .candidate,
                detail: "recent composer activity"
            )
        }
        return .init(
            state: .idle,
            confidence: .persisted,
            detail: "no active state signal"
        )
    }

    private func latestToolStatus(
        _ connection: SQLiteReadConnection,
        composerID: String,
        data: JSONObject
    ) throws -> (tool: String?, result: String?) {
        guard
            let headers = data["fullConversationHeadersOnly"]
                as? [Any]
        else {
            return (nil, nil)
        }
        for value in headers.suffix(20).reversed() {
            guard let header = value as? JSONObject,
                let bubbleID = Self.string(header["bubbleId"])
            else {
                continue
            }
            let bubble = try readDiskObject(
                connection,
                key: "bubbleId:\(composerID):\(bubbleID)"
            )
            guard let toolData = bubble["toolFormerData"] as? JSONObject
            else {
                continue
            }
            let additional = toolData["additionalData"] as? JSONObject
            return (
                Self.string(toolData["status"]),
                Self.string(additional?["status"])
            )
        }
        return (nil, nil)
    }

    private func readDiskObject(
        _ connection: SQLiteReadConnection,
        key: String
    ) throws -> JSONObject {
        let values: [String] = try connection.withRows(
            "SELECT value FROM cursorDiskKV WHERE key = ?",
            bindings: [key]
        ) { row in
            try row.text(0, allowBlob: true)
        }
        return Self.parseObject(values.first)
    }

    private func readItemObject(
        _ connection: SQLiteReadConnection,
        key: String
    ) throws -> JSONObject {
        let values: [String] = try connection.withRows(
            "SELECT value FROM ItemTable WHERE key = ?",
            bindings: [key]
        ) { row in
            try row.text(0, allowBlob: true)
        }
        return Self.parseObject(values.first)
    }

    private func loadWorkspacePaths() -> [String: String] {
        guard
            let directories = try? FileManager.default.contentsOfDirectory(
                at: workspaceStorageURL,
                includingPropertiesForKeys: [.isDirectoryKey],
                options: [.skipsHiddenFiles]
            )
        else {
            return [:]
        }
        var paths: [String: String] = [:]
        for directory in directories {
            let workspaceFile = directory.appendingPathComponent(
                "workspace.json"
            )
            guard let data = try? Data(contentsOf: workspaceFile),
                let payload = try? JSONSerialization.jsonObject(with: data)
                    as? JSONObject,
                let folder = Self.string(payload["folder"]),
                let path = Self.fileURIPath(folder)
            else {
                continue
            }
            paths[directory.lastPathComponent] = path
        }
        return paths
    }

    private static func parseObject(_ text: String?) -> JSONObject {
        guard let text,
            let data = text.data(using: .utf8),
            let payload = try? JSONSerialization.jsonObject(with: data)
                as? JSONObject
        else {
            return [:]
        }
        return payload
    }

    private static func string(_ value: Any?) -> String? {
        guard let value = value as? String, !value.isEmpty else {
            return nil
        }
        return value
    }

    private static func bool(_ value: Any?) -> Bool {
        value as? Bool ?? false
    }

    private static func nonEmptyArray(_ value: Any?) -> Bool {
        guard let value = value as? [Any] else { return false }
        return !value.isEmpty
    }

    private static func fileURIPath(_ uri: String) -> String? {
        guard let url = URL(string: uri), url.isFileURL else {
            return nil
        }
        return url.path
    }

    private static func embeddedWorkspacePath(
        _ header: JSONObject
    ) -> String? {
        let agentLocation = header["agentLocation"] as? JSONObject
        let environment = agentLocation?["environment"] as? JSONObject
        let workspaceIdentifier =
            header["workspaceIdentifier"] as? JSONObject
        let candidates = [
            environment?["uri"] as? JSONObject,
            workspaceIdentifier?["uri"] as? JSONObject,
        ]
        for uri in candidates.compactMap({ $0 }) {
            if let path = string(uri["fsPath"]) ?? string(uri["path"]) {
                return path
            }
            if let external = string(uri["external"]),
                let path = fileURIPath(external)
            {
                return path
            }
        }
        return nil
    }

    private static func exactSelectedID(
        _ rawSelectedID: String?,
        sessions: [AgentSession]
    ) -> String? {
        guard let rawSelectedID else { return nil }
        let matches = sessions.filter {
            $0.nativeID == rawSelectedID && $0.workspacePath != nil
        }
        return matches.count == 1 ? rawSelectedID : nil
    }

    private static func session(
        _ session: AgentSession,
        selected: Bool,
        state: (SessionState, DeckConfidence, String)? = nil
    ) -> AgentSession {
        AgentSession(
            providerID: session.providerID,
            nativeID: session.nativeID,
            capabilities: session.capabilities,
            icon: session.icon,
            title: session.title,
            workspaceID: session.workspaceID,
            workspacePath: session.workspacePath,
            state: state?.0 ?? session.state,
            confidence: state?.1 ?? session.confidence,
            stateDetail: state?.2 ?? session.stateDetail,
            selected: selected,
            lastActivityAtMilliseconds:
                session.lastActivityAtMilliseconds,
            commands: session.commands
        )
    }

    public func focus(
        nativeSessionID: String
    ) async throws -> ProviderActionResult {
        try await actionGate.perform {
            try await self.performFocus(
                nativeSessionID: nativeSessionID
            )
        }
    }

    private func performFocus(
        nativeSessionID: String
    ) async throws -> ProviderActionResult {
        let started = ContinuousClock.now
        let before = try await snapshot()
        guard
            before.sessions.contains(where: {
                $0.nativeID == nativeSessionID
                    && $0.capabilities.contains(.focusSession)
            })
        else {
            return actionResult(
                accepted: false,
                verdict: "INVALID_TARGET",
                message: "The requested Cursor session is not focusable.",
                started: started,
                details: ["session_id": .string(nativeSessionID)]
            )
        }

        try await automation.activate(bundleID: Self.bundleID)
        let activated = try await snapshot()
        var shortcutIndex: Int?
        if activated.selectedNativeSessionID != nativeSessionID {
            let order = try sidebarOrder()
            guard let index = order.firstIndex(of: nativeSessionID) else {
                return actionResult(
                    accepted: false,
                    verdict: "UNSUPPORTED_SIDEBAR_SHORTCUT",
                    message:
                        "The target is absent from Cursor's current sidebar order.",
                    started: started,
                    executed: true,
                    details: ["session_id": .string(nativeSessionID)]
                )
            }
            shortcutIndex = index + 1
            guard try await isFrontmost() else {
                return actionResult(
                    accepted: false,
                    verdict: "CURSOR_NOT_FOREGROUND",
                    message: "Cursor was not foreground before keyboard injection.",
                    started: started,
                    executed: true,
                    details: ["session_id": .string(nativeSessionID)]
                )
            }
            let latest = try await snapshot()
            let latestOrder = try sidebarOrder()
            guard
                latest.selectedNativeSessionID
                    == activated.selectedNativeSessionID,
                latestOrder == order,
                latestOrder.indices.contains(index),
                latestOrder[index] == nativeSessionID,
                try await isFrontmost()
            else {
                return actionResult(
                    accepted: false,
                    verdict: "STALE_PREFLIGHT",
                    message:
                        "Cursor selection or sidebar order changed before focus dispatch.",
                    started: started,
                    executed: false,
                    details: ["session_id": .string(nativeSessionID)]
                )
            }
            try await sendCursorSidebarShortcut(index: index + 1)
        }

        let deadline = ContinuousClock.now.advanced(by: .seconds(5))
        repeat {
            let current = try await snapshot()
            if current.selectedNativeSessionID == nativeSessionID,
                try await isFrontmost()
            {
                activityStore.acknowledge(
                    nativeSessionID,
                    observedAtMilliseconds: clock()
                )
                var details: [String: JSONValue] = [
                    "session_id": .string(nativeSessionID),
                    "strategy": .string(
                        shortcutIndex == nil
                            ? "activate_application"
                            : "sidebar_shortcut"
                    ),
                ]
                if let shortcutIndex {
                    details["shortcut_index"] = .integer(
                        Int64(shortcutIndex)
                    )
                }
                return actionResult(
                    accepted: true,
                    verdict: "FOCUS_VERIFIED",
                    message:
                        "The requested session is selected and Cursor is foreground.",
                    started: started,
                    executed: true,
                    details: details
                )
            }
            try await Task.sleep(for: .milliseconds(100))
        } while ContinuousClock.now < deadline

        return actionResult(
            accepted: false,
            verdict: "FOCUS_UNVERIFIED",
            message: "Cursor did not select the requested session before timeout.",
            started: started,
            executed: true,
            details: ["session_id": .string(nativeSessionID)]
        )
    }

    public func openNew() async throws -> ProviderActionResult {
        try await actionGate.perform {
            try await self.performOpenNew()
        }
    }

    private func performOpenNew() async throws -> ProviderActionResult {
        let started = ContinuousClock.now
        try await automation.activate(bundleID: Self.bundleID)
        guard try await isFrontmost() else {
            return actionResult(
                accepted: false,
                verdict: "CURSOR_NOT_FOREGROUND",
                message:
                    "Cursor was not foreground, so no New Agent shortcut was sent.",
                started: started,
                executed: false
            )
        }
        try await automation.postShortcut(
            keyCode: 45,
            flags: [.maskAlternate, .maskCommand],
            bundleID: Self.bundleID
        )
        try await Task.sleep(for: .milliseconds(500))
        guard try await isFrontmost() else {
            return actionResult(
                accepted: false,
                verdict: "CURSOR_LOST_FOREGROUND",
                message: "Cursor lost foreground before the New Agent shortcut.",
                started: started,
                executed: true
            )
        }
        try await automation.postShortcut(
            keyCode: 45,
            flags: .maskCommand,
            bundleID: Self.bundleID
        )
        guard try await isFrontmost() else {
            return actionResult(
                accepted: false,
                verdict: "CURSOR_LOST_FOREGROUND",
                message: "Cursor lost foreground after the New Agent shortcut.",
                started: started,
                executed: true
            )
        }
        return actionResult(
            accepted: true,
            verdict: "NEW_AGENT_VIEW_REQUESTED",
            message: "Cursor is foreground and the blank New Agent view was requested.",
            started: started,
            executed: true
        )
    }

    public func executeCommand(
        nativeSessionID: String,
        commandID: CommandID
    ) async throws -> ProviderActionResult {
        try await actionGate.perform {
            try await self.performCommand(
                nativeSessionID: nativeSessionID,
                commandID: commandID
            )
        }
    }

    private func performCommand(
        nativeSessionID: String,
        commandID: CommandID
    ) async throws -> ProviderActionResult {
        guard Self.commands.contains(commandID) else {
            return ProviderActionResult(
                accepted: false,
                verdict: "COMMAND_UNSUPPORTED",
                details: [
                    "message": .string(
                        "Cursor does not support \(commandID.rawValue)."
                    )
                ]
            )
        }
        let before = try await snapshot()
        guard before.selectedNativeSessionID == nativeSessionID,
            try await isFrontmost()
        else {
            return ProviderActionResult(
                accepted: false,
                verdict: "TARGET_UNVERIFIED",
                details: [
                    "message": .string(
                        "Cursor target is not uniquely selected and frontmost."
                    )
                ]
            )
        }
        let latest = try await snapshot()
        guard latest.selectedNativeSessionID == nativeSessionID,
            try await isFrontmost()
        else {
            return ProviderActionResult(
                accepted: false,
                verdict: "STALE_PREFLIGHT",
                details: [
                    "message": .string(
                        "Cursor target changed before command dispatch."
                    )
                ]
            )
        }

        let dispatched: ProviderActionResult
        switch commandID {
        case .accept:
            dispatched = try await automation.dispatchCommandEnter(
                bundleID: Self.bundleID,
                inputMarker: Self.inputMarker,
                focusKeyCode: 37,
                targetVerifier: {
                    try await self.isSelected(nativeSessionID)
                }
            )
        case .createPR:
            dispatched = try await automation.dispatchText(
                "Open a pull request for the current branch.",
                bundleID: Self.bundleID,
                inputMarker: Self.inputMarker,
                emptyPlaceholderValue: Self.emptyInputPlaceholder,
                focusKeyCode: 37,
                submitCount: 1,
                targetVerifier: {
                    try await self.isSelected(nativeSessionID)
                }
            )
        case .commitPush:
            dispatched = try await automation.dispatchText(
                "Commit the current changes with a Conventional Commit message and push the current branch.",
                bundleID: Self.bundleID,
                inputMarker: Self.inputMarker,
                emptyPlaceholderValue: Self.emptyInputPlaceholder,
                focusKeyCode: 37,
                submitCount: 1,
                targetVerifier: {
                    try await self.isSelected(nativeSessionID)
                }
            )
        case .compact:
            dispatched = try await automation.dispatchText(
                "/summarize",
                bundleID: Self.bundleID,
                inputMarker: Self.inputMarker,
                emptyPlaceholderValue: Self.emptyInputPlaceholder,
                focusKeyCode: 37,
                submitCount: 2,
                targetVerifier: {
                    try await self.isSelected(nativeSessionID)
                }
            )
        }
        let after = try await snapshot()
        guard dispatched.accepted,
            after.selectedNativeSessionID == nativeSessionID,
            try await isFrontmost()
        else {
            return ProviderActionResult(
                accepted: false,
                verdict: "DISPATCH_UNVERIFIED",
                details: dispatched.details.merging([
                    "session_id": .string(nativeSessionID),
                    "command_id": .string(commandID.rawValue),
                ]) { current, _ in current }
            )
        }
        return ProviderActionResult(
            accepted: true,
            verdict: dispatched.verdict,
            details: dispatched.details.merging([
                "session_id": .string(nativeSessionID),
                "command_id": .string(commandID.rawValue),
            ]) { current, _ in current }
        )
    }

    public func recordHook(
        _ payload: ProviderHookPayload,
        observedAtMilliseconds: Int64
    ) async throws -> ActivityObservation {
        guard let sessionID = payload.conversationID, !sessionID.isEmpty else {
            throw ProviderOperationError.invalidHook(
                "Cursor hook event is missing conversation_id"
            )
        }
        let current = try await snapshot()
        guard
            current.sessions.contains(where: {
                $0.nativeID == sessionID
            })
        else {
            throw ProviderOperationError.invalidHook(
                "Cursor hook conversation_id is not in current inventory"
            )
        }
        return try activityStore.record(
            payload,
            observedAtMilliseconds: observedAtMilliseconds
        )
    }

    private func isSelected(_ nativeSessionID: String) async throws -> Bool {
        try await snapshot().selectedNativeSessionID == nativeSessionID
    }

    private func sidebarOrder() throws -> [String] {
        let connection = try SQLiteReadConnection(url: databaseURL)
        let settings = try readItemObject(
            connection,
            key: "cursor/glassSidebarSettings"
        )
        guard Self.string(settings["groupBy"]) == "repository" else {
            throw CursorProviderError.readFailed(
                "Cursor sidebar shortcuts require repository grouping"
            )
        }
        guard let sortBy = Self.string(settings["sortAgentsBy"]),
            sortBy == "updated" || sortBy == "created"
        else {
            throw CursorProviderError.readFailed(
                "Unsupported Cursor sidebar sort"
            )
        }
        let createdAtExpression: String
        if sortBy == "created" {
            let columns = Set(
                try connection.withRows(
                    "PRAGMA table_info(\"composerHeaders\")"
                ) { row in
                    try row.text(1)
                }
            )
            guard columns.contains("createdAt") else {
                throw CursorProviderError.unsupportedTableSchema(
                    table: "composerHeaders",
                    missingColumns: ["createdAt"]
                )
            }
            createdAtExpression = "createdAt"
        } else {
            createdAtExpression = "0"
        }
        guard
            let sectionOrders =
                settings["sectionOrderByGroupBy"] as? JSONObject,
            let sectionOrder = sectionOrders["repository"] as? [String]
        else {
            throw CursorProviderError.readFailed(
                "Cursor repository section order is invalid"
            )
        }
        let memberships = try readItemObject(
            connection,
            key: "glass.localAgentProjectMembership.v1"
        )
        let candidates: [SidebarCandidate] = try connection.withRows(
            """
            SELECT composerId, \(createdAtExpression), lastUpdatedAt,
                   isArchived, isSubagent, value
            FROM composerHeaders
            """
        ) { row in
            guard let sessionID = try row.text(0),
                Self.membershipIncludes(
                    sessionID: sessionID,
                    memberships: memberships
                ),
                try !row.boolean(3),
                try !row.boolean(4)
            else {
                return nil
            }
            let header = Self.parseObject(
                try row.text(5, allowBlob: true)
            )
            guard !Self.bool(header["isDraft"]),
                !Self.bool(header["isEphemeral"])
            else {
                return nil
            }
            return SidebarCandidate(
                sessionID: sessionID,
                createdAt: row.integer(1) ?? 0,
                updatedAt: row.integer(2) ?? 0,
                workspaceID: Self.workspaceIdentifier(header),
                repositoryName: Self.repositoryName(header)
            )
        }
        try connection.commit()

        let pinnedIDs = try pinnedSessionIDs()
        let sorted: ([SidebarCandidate]) -> [SidebarCandidate] = { values in
            values.sorted {
                let left = sortBy == "updated" ? $0.updatedAt : $0.createdAt
                let right = sortBy == "updated" ? $1.updatedAt : $1.createdAt
                if left != right { return left > right }
                return $0.sessionID < $1.sessionID
            }
        }
        var ordered = sorted(
            candidates.filter { pinnedIDs.contains($0.sessionID) }
        )
        let unpinned = candidates.filter {
            !pinnedIDs.contains($0.sessionID)
        }
        for sectionID in sectionOrder {
            ordered.append(
                contentsOf: sorted(
                    unpinned.filter {
                        Self.section(
                            for: $0,
                            sectionOrder: sectionOrder
                        ) == sectionID
                    }
                )
            )
        }
        return ordered.map(\.sessionID)
    }

    static func membershipIncludes(
        sessionID: String,
        memberships: [String: Any]
    ) -> Bool {
        memberships.isEmpty || memberships[sessionID] != nil
    }

    private func pinnedSessionIDs() throws -> Set<String> {
        let url =
            workspaceStorageURL
            .appendingPathComponent("empty-window", isDirectory: true)
            .appendingPathComponent("state.vscdb")
        guard FileManager.default.fileExists(atPath: url.path) else {
            return []
        }
        let connection = try SQLiteReadConnection(url: url)
        let values: [String] = try connection.withRows(
            "SELECT value FROM ItemTable WHERE key = ?",
            bindings: ["cursor/pinnedComposers"]
        ) { row in
            try row.text(0, allowBlob: true)
        }
        try connection.commit()
        guard let value = values.first,
            let data = value.data(using: .utf8),
            let identifiers = try JSONSerialization.jsonObject(with: data)
                as? [String]
        else {
            if values.isEmpty { return [] }
            throw CursorProviderError.readFailed(
                "Cursor pinned-agent state is malformed"
            )
        }
        return Set(identifiers)
    }

    private func sendCursorSidebarShortcut(index: Int) async throws {
        guard index > 0 else {
            throw ProviderOperationError.system(
                "Cursor sidebar shortcut index must be positive"
            )
        }
        let keyCodes: [Int: CGKeyCode] = [
            1: 18, 2: 19, 3: 20, 4: 21, 5: 23,
            6: 22, 7: 26, 8: 28, 9: 25,
        ]
        let direct = min(index, 9)
        guard let keyCode = keyCodes[direct] else {
            throw ProviderOperationError.system(
                "Cursor sidebar shortcut is invalid"
            )
        }
        try await automation.postShortcut(
            keyCode: keyCode,
            flags: .maskCommand,
            bundleID: Self.bundleID
        )
        if index > direct {
            try await Task.sleep(for: .milliseconds(150))
            for _ in direct..<index {
                try await automation.postShortcut(
                    keyCode: 125,
                    flags: .maskAlternate,
                    bundleID: Self.bundleID
                )
                try await Task.sleep(for: .milliseconds(120))
            }
        }
    }

    private func actionResult(
        accepted: Bool,
        verdict: String,
        message: String,
        started: ContinuousClock.Instant,
        executed: Bool? = nil,
        details: [String: JSONValue] = [:]
    ) -> ProviderActionResult {
        let duration = started.duration(to: .now)
        let milliseconds =
            duration.components.seconds * 1_000
            + Int64(duration.components.attoseconds / 1_000_000_000_000_000)
        return ProviderActionResult(
            accepted: accepted,
            verdict: verdict,
            details: details.merging([
                "executed": .boolean(executed ?? accepted),
                "elapsed_ms": .integer(milliseconds),
                "verdict": .string(verdict),
                "message": .string(message),
            ]) { current, _ in current }
        )
    }

    private static func workspaceIdentifier(
        _ header: JSONObject
    ) -> String? {
        let workspace = header["workspaceIdentifier"] as? JSONObject
        return string(workspace?["id"])
    }

    private static func repositoryName(
        _ header: JSONObject
    ) -> String? {
        if let location = header["agentLocation"] as? JSONObject,
            let source = string(location["sourceRepoRootPath"])
        {
            return URL(fileURLWithPath: source).lastPathComponent
        }
        guard let repositories = header["trackedGitRepos"] as? [Any],
            let first = repositories.first as? JSONObject,
            let path = string(first["repoPath"])
        else {
            return nil
        }
        let components = URL(fileURLWithPath: path).pathComponents
        if let index = components.firstIndex(of: "worktrees"),
            index + 1 < components.count
        {
            return components[index + 1]
        }
        return URL(fileURLWithPath: path).lastPathComponent
    }

    private static func section(
        for candidate: SidebarCandidate,
        sectionOrder: [String]
    ) -> String? {
        if let repositoryName = candidate.repositoryName {
            let matches = sectionOrder.filter {
                $0.hasPrefix("repo:")
                    && !$0.contains("|")
                    && $0.hasSuffix("/\(repositoryName)")
            }
            if matches.count == 1 {
                return matches[0]
            }
        }
        guard let workspaceID = candidate.workspaceID else { return nil }
        let workspaceSection = "workspace:\(workspaceID)"
        return sectionOrder.contains(workspaceSection)
            ? workspaceSection
            : nil
    }
}

private typealias JSONObject = [String: Any]

private struct CursorSessionRow {
    let composerID: String
    let workspaceID: String
    let lastUpdatedAtMilliseconds: Int64?
    let isArchived: Bool
    let isSubagent: Bool
    let header: JSONObject
}

private struct CursorCandidate {
    let session: AgentSession
    let generationID: String?
}

private struct SidebarCandidate {
    let sessionID: String
    let createdAt: Int64
    let updatedAt: Int64
    let workspaceID: String?
    let repositoryName: String?
}

private struct InferredState {
    let state: SessionState
    let confidence: DeckConfidence
    let detail: String
}
