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
        capabilities: []
    )

    public static let activeSignalTTLMilliseconds: Int64 = 5 * 60 * 1_000

    private let databaseURL: URL
    private let workspaceStorageURL: URL
    private let activeSignalTTLMilliseconds: Int64
    private let clock: @Sendable () -> Int64

    public init(
        databaseURL: URL = CursorProvider.defaultDatabaseURL,
        workspaceStorageURL: URL =
            CursorProvider.defaultWorkspaceStorageURL,
        activeSignalTTLMilliseconds: Int64 =
            CursorProvider.activeSignalTTLMilliseconds,
        clock: @escaping @Sendable () -> Int64 = {
            Int64(Date().timeIntervalSince1970 * 1_000)
        }
    ) {
        self.databaseURL = databaseURL
        self.workspaceStorageURL = workspaceStorageURL
        self.activeSignalTTLMilliseconds = activeSignalTTLMilliseconds
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
                sessions: candidates
            )
            let sessions = candidates.map {
                Self.session(
                    $0,
                    selected: $0.nativeID == selectedID
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
        false
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
    ) throws -> [AgentSession] {
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

        var sessions: [AgentSession] = []
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
            guard let lastActivityAtMilliseconds =
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
            sessions.append(
                AgentSession(
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
                    lastActivityAtMilliseconds: lastActivityAtMilliseconds
                )
            )
        }
        return sessions.sorted {
            if $0.lastActivityAtMilliseconds
                != $1.lastActivityAtMilliseconds
            {
                return $0.lastActivityAtMilliseconds
                    > $1.lastActivityAtMilliseconds
            }
            return $0.id < $1.id
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
        guard let headers = data["fullConversationHeadersOnly"]
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
        guard let directories = try? FileManager.default.contentsOfDirectory(
            at: workspaceStorageURL,
            includingPropertiesForKeys: [.isDirectoryKey],
            options: [.skipsHiddenFiles]
        ) else {
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
        selected: Bool
    ) -> AgentSession {
        AgentSession(
            providerID: session.providerID,
            nativeID: session.nativeID,
            capabilities: session.capabilities,
            icon: session.icon,
            title: session.title,
            workspaceID: session.workspaceID,
            workspacePath: session.workspacePath,
            state: session.state,
            confidence: session.confidence,
            stateDetail: session.stateDetail,
            selected: selected,
            lastActivityAtMilliseconds:
                session.lastActivityAtMilliseconds,
            commands: session.commands
        )
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

private struct InferredState {
    let state: SessionState
    let confidence: DeckConfidence
    let detail: String
}
