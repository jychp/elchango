@preconcurrency import ApplicationServices
import Darwin
import ElChangoCore
import Foundation

public enum ClaudeCodeProviderError: LocalizedError {
    case directoryNotFound(URL)
    case invalidRecord(URL, String)
    case transcriptMapping(
        desktopSessionID: String,
        transcriptCount: Int
    )
    case readFailed(String)

    public var errorDescription: String? {
        switch self {
        case .directoryNotFound(let url):
            "Claude inventory directory not found: \(url.path)"
        case .invalidRecord(let url, let message):
            "\(url.path): \(message)"
        case .transcriptMapping(let sessionID, let count):
            """
            Claude Desktop session transcript mapping must be unique for \
            \(sessionID); found \(count)
            """
        case .readFailed(let message):
            "Claude Desktop inventory read failed: \(message)"
        }
    }
}

public actor ClaudeCodeProvider: AgentProvider {
    public nonisolated let descriptor = ProviderDescriptor(
        id: "claude-code",
        displayName: "Claude",
        icon: .claude,
        capabilities: [.focusSession, .newSession, .executeCommand]
    )

    public static let maximumMetadataPrefixBytes = 64 * 1_024
    public static let bundleID = "com.anthropic.claudefordesktop"
    public static let inputMarker = "tiptapProseMirrorProseMirror-focused"
    public static let commands: Set<CommandID> = [
        .accept, .createPR, .commitPush, .compact,
    ]

    private let desktopSessionsRootURL: URL
    private let projectsRootURL: URL
    private let desktopConfigURL: URL
    private let clock: @Sendable () -> Int64
    private let activityStore: ClaudeActivityStore
    private let automation: any NativeAutomating
    private let actionGate: PrivilegedActionGate
    private var recordCache: [URL: CachedClaudeRecord] = [:]

    public init(
        desktopSessionsRootURL: URL =
            ClaudeCodeProvider.defaultDesktopSessionsRootURL,
        projectsRootURL: URL = ClaudeCodeProvider.defaultProjectsRootURL,
        desktopConfigURL: URL = ClaudeCodeProvider.defaultDesktopConfigURL,
        activityStore: ClaudeActivityStore = ClaudeActivityStore(),
        automation: any NativeAutomating = NativeAutomation(),
        actionGate: PrivilegedActionGate = PrivilegedActionGate(),
        clock: @escaping @Sendable () -> Int64 = {
            Int64(Date().timeIntervalSince1970 * 1_000)
        }
    ) {
        self.desktopSessionsRootURL = desktopSessionsRootURL
        self.projectsRootURL = projectsRootURL
        self.desktopConfigURL = desktopConfigURL
        self.activityStore = activityStore
        self.automation = automation
        self.actionGate = actionGate
        self.clock = clock
    }

    public func snapshot() async throws -> ProviderSnapshot {
        do {
            let observedAtMilliseconds = clock()
            let records = try readRecords()
            let transcripts = try transcriptsBySessionID()
            let visibleRecords = records.filter { !$0.isArchived }
            let selectedID = Self.selectedNativeSessionID(
                from: visibleRecords
            )
            let shortcuts =
                (try? shortcutOrder(
                    records: visibleRecords
                )) ?? []
            var sessions: [AgentSession] = []
            for record in visibleRecords {
                let transcriptCount =
                    transcripts[record.cliSessionID, default: []].count
                guard transcriptCount == 1 else {
                    throw ClaudeCodeProviderError.transcriptMapping(
                        desktopSessionID: record.desktopSessionID,
                        transcriptCount: transcriptCount
                    )
                }
                let activity = activityStore.state(
                    for: record.cliSessionID,
                    observedAtMilliseconds: observedAtMilliseconds
                )
                sessions.append(
                    AgentSession(
                        providerID: descriptor.id,
                        nativeID: record.desktopSessionID,
                        capabilities: Self.sessionCapabilities(
                            desktopSessionID: record.desktopSessionID,
                            shortcutOrder: shortcuts
                        ),
                        icon: descriptor.icon,
                        title: record.title ?? "Untitled Claude session",
                        workspaceID: record.originCWD,
                        workspacePath: record.cwd,
                        state: activity?.0 ?? .idle,
                        confidence: activity?.1 ?? .persisted,
                        stateDetail: activity?.2
                            ?? "Persistent Claude Desktop session; no fresh hook signal",
                        selected: record.desktopSessionID == selectedID,
                        lastActivityAtMilliseconds:
                            record.lastActivityAtMilliseconds,
                        commands: Self.commands
                    )
                )
            }
            sessions.sort {
                if $0.lastActivityAtMilliseconds
                    != $1.lastActivityAtMilliseconds
                {
                    return $0.lastActivityAtMilliseconds
                        > $1.lastActivityAtMilliseconds
                }
                return $0.id < $1.id
            }
            return ProviderSnapshot(
                providerID: descriptor.id,
                capabilities: descriptor.capabilities,
                observedAtMilliseconds: observedAtMilliseconds,
                selectedNativeSessionID: selectedID,
                sessions: sessions,
                source: desktopSessionsRootURL.path,
                readOnly: true
            )
        } catch let error as ClaudeCodeProviderError {
            throw error
        } catch {
            throw ClaudeCodeProviderError.readFailed(
                error.localizedDescription
            )
        }
    }

    public func isFrontmost() async throws -> Bool {
        await automation.frontmostBundleID() == Self.bundleID
    }

    public static var defaultDesktopSessionsRootURL: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(
                "Library/Application Support/Claude/claude-code-sessions",
                isDirectory: true
            )
    }

    public static var defaultProjectsRootURL: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".claude/projects", isDirectory: true)
    }

    public static var defaultDesktopConfigURL: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(
                "Library/Application Support/Claude/claude_desktop_config.json"
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
        DebugTrace.emit("claude-focus", "focus.start")
        let started = ContinuousClock.now
        let beforeRecords = try readRecords().filter { !$0.isArchived }
        DebugTrace.emit(
            "claude-focus",
            "focus.inventory_loaded count=\(beforeRecords.count)"
        )
        guard
            let target = beforeRecords.first(where: {
                $0.desktopSessionID == nativeSessionID
            })
        else {
            throw ProviderOperationError.targetUnverified(
                "unknown non-archived Claude session: \(nativeSessionID)"
            )
        }
        try await automation.activate(bundleID: Self.bundleID)
        DebugTrace.emit("claude-focus", "focus.activated")
        let order = try shortcutOrder(records: beforeRecords)
        guard let index = order.firstIndex(of: nativeSessionID) else {
            return actionResult(
                accepted: false,
                verdict: "FOCUS_UNSUPPORTED",
                message:
                    "Claude session is absent from the persisted sidebar shortcuts.",
                started: started,
                executed: false,
                details: ["session_id": .string(nativeSessionID)]
            )
        }
        let beforeMaximum =
            beforeRecords.compactMap(
                \.lastFocusedAtMilliseconds
            ).max() ?? -1
        let targetAlreadySelected =
            target.lastFocusedAtMilliseconds == beforeMaximum
            && beforeRecords.filter {
                $0.lastFocusedAtMilliseconds == beforeMaximum
            }.count == 1

        if !targetAlreadySelected {
            DebugTrace.emit(
                "claude-focus",
                "focus.preflight.start shortcut=\(index + 1)"
            )
            let latestRecords = try readRecords().filter {
                !$0.isArchived
            }
            let latestOrder = try shortcutOrder(records: latestRecords)
            guard latestOrder == order,
                latestOrder.indices.contains(index),
                latestOrder[index] == nativeSessionID,
                Self.selectedNativeSessionID(from: latestRecords)
                    == Self.selectedNativeSessionID(from: beforeRecords),
                try await isFrontmost()
            else {
                return actionResult(
                    accepted: false,
                    verdict: "STALE_PREFLIGHT",
                    message:
                        "Claude selection or sidebar order changed before focus dispatch.",
                    started: started,
                    executed: false,
                    details: ["session_id": .string(nativeSessionID)]
                )
            }
            DebugTrace.emit("claude-focus", "focus.preflight.complete")
            try await sendClaudeSidebarShortcut(index: index + 1)
            DebugTrace.emit("claude-focus", "focus.shortcut_posted")
        }
        guard try await isFrontmost() else {
            return actionResult(
                accepted: false,
                verdict: "FOCUS_DISPATCH_UNVERIFIED",
                message: "Claude lost foreground identity after focus dispatch.",
                started: started,
                executed: !targetAlreadySelected,
                details: ["session_id": .string(nativeSessionID)]
            )
        }
        activityStore.acknowledge(
            target.cliSessionID,
            observedAtMilliseconds: clock()
        )
        DebugTrace.emit("claude-focus", "focus.complete")
        return actionResult(
            accepted: true,
            verdict: targetAlreadySelected
                ? "FOCUS_VERIFIED"
                : "FOCUS_DISPATCH_VERIFIED",
            message: targetAlreadySelected
                ? "Exact Claude Desktop session focus verified."
                : "Exact Claude sidebar shortcut dispatch verified.",
            started: started,
            executed: true,
            details: [
                "session_id": .string(nativeSessionID),
                "strategy": .string("sidebar_shortcut"),
                "shortcut_index": .integer(Int64(index + 1)),
            ]
        )
    }

    public func openNew() async throws -> ProviderActionResult {
        try await actionGate.perform {
            try await self.performOpenNew()
        }
    }

    private func performOpenNew() async throws -> ProviderActionResult {
        let started = ContinuousClock.now
        guard let url = URL(string: "claude://code/new") else {
            throw ProviderOperationError.system(
                "Claude new-session deep link is invalid"
            )
        }
        try await automation.open(url: url)
        return actionResult(
            accepted: true,
            verdict: "NEW_SESSION_REQUESTED",
            message: "Claude Desktop new Code session requested.",
            started: started,
            executed: true,
            details: ["deep_link": .string(url.absoluteString)]
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
                        "Claude does not support \(commandID.rawValue)."
                    )
                ]
            )
        }
        guard try await isSelected(nativeSessionID),
            try await isFrontmost()
        else {
            return ProviderActionResult(
                accepted: false,
                verdict: "TARGET_UNVERIFIED",
                details: [
                    "message": .string(
                        "Claude target is not uniquely selected and frontmost."
                    )
                ]
            )
        }
        guard try await isSelected(nativeSessionID),
            try await isFrontmost()
        else {
            return ProviderActionResult(
                accepted: false,
                verdict: "STALE_PREFLIGHT",
                details: [
                    "message": .string(
                        "Claude target changed before command dispatch."
                    )
                ]
            )
        }
        let dispatched: ProviderActionResult
        switch commandID {
        case .accept:
            dispatched = try await automation.dispatchCommandEnter(
                bundleID: Self.bundleID,
                inputMarker: nil,
                focusKeyCode: nil,
                targetVerifier: {
                    try await self.isSelected(nativeSessionID)
                }
            )
        case .createPR:
            dispatched = try await automation.dispatchText(
                "Open a pull request for the current branch.",
                bundleID: Self.bundleID,
                inputMarker: Self.inputMarker,
                focusKeyCode: nil,
                unfocusedPolicy: .bestEffort(
                    allowedAccessibilityRoles: ["AXGroup"]
                ),
                submitCount: 2,
                targetVerifier: {
                    try await self.isSelected(nativeSessionID)
                }
            )
        case .commitPush:
            dispatched = try await automation.dispatchText(
                "Commit the current changes with a Conventional Commit message and push the current branch.",
                bundleID: Self.bundleID,
                inputMarker: Self.inputMarker,
                focusKeyCode: nil,
                unfocusedPolicy: .bestEffort(
                    allowedAccessibilityRoles: ["AXGroup"]
                ),
                submitCount: 2,
                targetVerifier: {
                    try await self.isSelected(nativeSessionID)
                }
            )
        case .compact:
            dispatched = try await automation.dispatchText(
                "/compact",
                bundleID: Self.bundleID,
                inputMarker: Self.inputMarker,
                focusKeyCode: nil,
                unfocusedPolicy: .bestEffort(
                    allowedAccessibilityRoles: ["AXGroup"]
                ),
                submitCount: 2,
                targetVerifier: {
                    try await self.isSelected(nativeSessionID)
                }
            )
        }
        guard dispatched.accepted,
            try await isSelected(nativeSessionID),
            try await isFrontmost()
        else {
            return ProviderActionResult(
                accepted: false,
                verdict: "DISPATCH_UNVERIFIED",
                details: dispatched.details
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
        return try activityStore.record(
            payload,
            observedAtMilliseconds: observedAtMilliseconds
        )
    }

    private func isSelected(_ nativeSessionID: String) async throws -> Bool {
        let visibleRecords = try readRecords().filter { !$0.isArchived }
        return Self.selectedNativeSessionID(from: visibleRecords)
            == nativeSessionID
    }

    private func shortcutOrder(
        records: [ClaudeDesktopRecord]
    ) throws -> [String] {
        let root = try OrderedJSON(data: Data(contentsOf: desktopConfigURL))
        guard let epitaxy = root["preferences"]?["epitaxyPrefs"],
            let localSlice = epitaxy["dframe-local-slice"]
        else {
            throw ClaudeCodeProviderError.readFailed(
                "cannot read Claude sidebar order"
            )
        }
        let visibleIDs = Set(records.map(\.desktopSessionID))
        if let pinnedOrder = localSlice["pinnedOrder"]?.stringArray {
            let scopeKeys = Set(records.map(\.groupScopeKey))
            guard scopeKeys.count <= 1 else {
                throw ClaudeCodeProviderError.readFailed(
                    "Claude visible sessions span multiple sidebar group scopes"
                )
            }
            let groupScope = try scopeKeys.first.flatMap { scopeKey in
                try currentGroupScope(
                    epitaxy: epitaxy,
                    scopeKey: scopeKey
                )
            }
            let assignments = groupScope?.assignments ?? [:]
            var persisted: [String] = []
            for qualified in pinnedOrder
            where qualified.hasPrefix("code:") {
                let sessionID = String(qualified.dropFirst("code:".count))
                if visibleIDs.contains(sessionID),
                    assignments[qualified] == nil,
                    !persisted.contains(sessionID)
                {
                    persisted.append(sessionID)
                }
            }
            if let groupScope {
                for groupID in groupScope.groupIDs {
                    for qualified in groupScope.order[groupID, default: []]
                    where qualified.hasPrefix("code:") {
                        let sessionID = String(
                            qualified.dropFirst("code:".count)
                        )
                        guard
                            !visibleIDs.contains(sessionID)
                                || assignments[qualified] == groupID
                        else {
                            throw ClaudeCodeProviderError.readFailed(
                                "Claude group assignment and order disagree"
                            )
                        }
                        if visibleIDs.contains(sessionID),
                            !persisted.contains(sessionID)
                        {
                            persisted.append(sessionID)
                        }
                    }
                }
                let orderedIDs = Set(persisted)
                let missingAssigned = records.contains {
                    assignments["code:\($0.desktopSessionID)"] != nil
                        && !orderedIDs.contains($0.desktopSessionID)
                }
                guard !missingAssigned else {
                    throw ClaudeCodeProviderError.readFailed(
                        "Claude group order omits a visible assigned session"
                    )
                }
            }
            appendRemaining(
                records.filter {
                    assignments["code:\($0.desktopSessionID)"] == nil
                },
                to: &persisted
            )
            return persisted
        }
        guard
            let starred = epitaxy["starred-local-code-sessions"]?
                .stringArray,
            let assignmentEntries = localSlice["customGroupAssignments"]?
                .objectEntries,
            let groupEntries = localSlice["customGroupOrder"]?
                .objectEntries
        else {
            throw ClaudeCodeProviderError.readFailed(
                "cannot read Claude sidebar order"
            )
        }
        let assignedKeys = Set(assignmentEntries.map(\.0))
        var persisted: [String] = []
        for sessionID in starred.reversed()
        where visibleIDs.contains(sessionID)
            && !assignedKeys.contains("code:\(sessionID)")
        {
            if !persisted.contains(sessionID) {
                persisted.append(sessionID)
            }
        }
        for (_, group) in groupEntries {
            guard let ordered = group.stringArray else {
                throw ClaudeCodeProviderError.readFailed(
                    "Claude custom group entries must be string lists"
                )
            }
            for qualified in ordered
            where qualified.hasPrefix("code:") {
                let sessionID = String(qualified.dropFirst("code:".count))
                if visibleIDs.contains(sessionID),
                    !persisted.contains(sessionID)
                {
                    persisted.append(sessionID)
                }
            }
        }
        appendRemaining(records, to: &persisted)
        return persisted
    }

    private func currentGroupScope(
        epitaxy: OrderedJSON,
        scopeKey: String
    ) throws -> ClaudeGroupScope? {
        guard let scopes = epitaxy["dframe-group-scopes"] else {
            return nil
        }
        guard let scopeEntries = scopes.objectEntries else {
            throw ClaudeCodeProviderError.readFailed(
                "Claude group scopes must be an object"
            )
        }
        guard let scope = scopeEntries.first(where: { $0.0 == scopeKey })?.1
        else {
            return nil
        }
        guard let groups = scope["groups"]?.arrayValues,
            let assignmentEntries = scope["assignments"]?.objectEntries,
            let orderEntries = scope["order"]?.objectEntries
        else {
            throw ClaudeCodeProviderError.readFailed(
                "Claude group scope is malformed"
            )
        }
        let groupIDs = try groups.map { group in
            guard let groupID = group["id"]?.stringValue, !groupID.isEmpty
            else {
                throw ClaudeCodeProviderError.readFailed(
                    "Claude group ID must be a nonempty string"
                )
            }
            return groupID
        }
        var assignments: [String: String] = [:]
        for (qualified, value) in assignmentEntries {
            guard let groupID = value.stringValue else {
                throw ClaudeCodeProviderError.readFailed(
                    "Claude group assignments must contain string IDs"
                )
            }
            assignments[qualified] = groupID
        }
        var order: [String: [String]] = [:]
        for (groupID, value) in orderEntries {
            guard let qualifiedIDs = value.stringArray else {
                throw ClaudeCodeProviderError.readFailed(
                    "Claude group order must contain string lists"
                )
            }
            order[groupID] = qualifiedIDs
        }
        return ClaudeGroupScope(
            groupIDs: groupIDs,
            assignments: assignments,
            order: order
        )
    }

    private func appendRemaining(
        _ records: [ClaudeDesktopRecord],
        to persisted: inout [String]
    ) {
        let remaining =
            records
            .filter {
                !persisted.contains($0.desktopSessionID)
            }
            .sorted {
                if $0.lastActivityAtMilliseconds
                    != $1.lastActivityAtMilliseconds
                {
                    return $0.lastActivityAtMilliseconds
                        > $1.lastActivityAtMilliseconds
                }
                return $0.desktopSessionID < $1.desktopSessionID
            }
        persisted.append(contentsOf: remaining.map(\.desktopSessionID))
    }

    private static func sessionCapabilities(
        desktopSessionID: String,
        shortcutOrder: [String]
    ) -> Set<ProviderCapability> {
        shortcutOrder.contains(desktopSessionID)
            ? [.focusSession, .newSession, .executeCommand]
            : [.executeCommand]
    }

    private func sendClaudeSidebarShortcut(index: Int) async throws {
        let keyCodes: [Int: CGKeyCode] = [
            1: 18, 2: 19, 3: 20, 4: 21, 5: 23,
            6: 22, 7: 26, 8: 28, 9: 25,
        ]
        let direct = min(index, 9)
        guard let keyCode = keyCodes[direct] else {
            throw ProviderOperationError.system(
                "Claude sidebar shortcut is invalid"
            )
        }
        try await automation.postShortcut(
            keyCode: keyCode,
            flags: .maskCommand,
            bundleID: Self.bundleID
        )
        if index > direct {
            try await Task.sleep(for: .milliseconds(150))
            try await automation.postHeldModifierShortcut(
                modifierKeyCode: 59,
                keyCode: 48,
                flags: .maskControl,
                repeatCount: index - direct,
                bundleID: Self.bundleID
            )
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

    private func readRecords() throws -> [ClaudeDesktopRecord] {
        try requireDirectory(desktopSessionsRootURL)
        let accountDirectories = try directoryContents(
            desktopSessionsRootURL
        )
        var recordURLs: [URL] = []
        for accountDirectory in accountDirectories
        where try isDirectory(accountDirectory) {
            for workspaceDirectory in try directoryContents(accountDirectory)
            where try isDirectory(workspaceDirectory) {
                recordURLs.append(
                    contentsOf: try contentsOfDirectory(
                        workspaceDirectory
                    ).filter {
                        $0.lastPathComponent.hasPrefix("local_")
                            && $0.pathExtension == "json"
                    }
                )
            }
        }
        let orderedURLs = recordURLs.sorted { $0.path < $1.path }
        let currentURLs = Set(orderedURLs)
        recordCache = recordCache.filter {
            currentURLs.contains($0.key)
        }
        return try orderedURLs.map { url in
            let status = try Self.fileStatus(url)
            if let cached = recordCache[url],
                cached.modifiedAtNanoseconds
                    == status.modifiedAtNanoseconds,
                cached.size == status.size
            {
                return cached.record
            }
            let record = try Self.readRecord(url)
            recordCache[url] = CachedClaudeRecord(
                modifiedAtNanoseconds: status.modifiedAtNanoseconds,
                size: status.size,
                record: record
            )
            return record
        }
    }

    private func transcriptsBySessionID() throws -> [String: [URL]] {
        try requireDirectory(projectsRootURL)
        var transcripts: [String: [URL]] = [:]
        for projectDirectory in try directoryContents(projectsRootURL)
        where try isDirectory(projectDirectory) {
            for transcriptURL in try contentsOfDirectory(projectDirectory)
            where transcriptURL.pathExtension == "jsonl" {
                transcripts[
                    transcriptURL.deletingPathExtension().lastPathComponent,
                    default: []
                ].append(transcriptURL)
            }
        }
        return transcripts
    }

    private func requireDirectory(_ url: URL) throws {
        var isDirectory: ObjCBool = false
        guard
            FileManager.default.fileExists(
                atPath: url.path,
                isDirectory: &isDirectory
            ), isDirectory.boolValue
        else {
            throw ClaudeCodeProviderError.directoryNotFound(url)
        }
    }

    private func directoryContents(_ url: URL) throws -> [URL] {
        try contentsOfDirectory(url).filter {
            !$0.lastPathComponent.hasPrefix(".")
        }
    }

    private func contentsOfDirectory(_ url: URL) throws -> [URL] {
        try FileManager.default.contentsOfDirectory(
            at: url,
            includingPropertiesForKeys: [.isDirectoryKey],
            options: []
        )
    }

    private func isDirectory(_ url: URL) throws -> Bool {
        try url.resourceValues(forKeys: [.isDirectoryKey]).isDirectory == true
    }

    private static func readRecord(
        _ url: URL
    ) throws -> ClaudeDesktopRecord {
        let values = try readTopLevelMetadata(url)
        let desktopSessionID = try requiredString(
            values,
            key: "sessionId",
            url: url
        )
        guard
            url.deletingPathExtension().lastPathComponent
                == desktopSessionID
        else {
            throw ClaudeCodeProviderError.invalidRecord(
                url,
                "filename must match the Desktop sessionId"
            )
        }
        let cliSessionID = try requiredString(
            values,
            key: "cliSessionId",
            url: url
        )
        guard let archivedNumber = values["isArchived"] as? NSNumber,
            CFGetTypeID(archivedNumber) == CFBooleanGetTypeID()
        else {
            throw ClaudeCodeProviderError.invalidRecord(
                url,
                "isArchived must be a boolean"
            )
        }
        let isArchived = archivedNumber.boolValue
        let title: String?
        if values["title"] is NSNull || values["title"] == nil {
            title = nil
        } else if let value = values["title"] as? String {
            title = value
        } else {
            throw ClaudeCodeProviderError.invalidRecord(
                url,
                "title must be a string or null"
            )
        }
        let lastFocusedAtMilliseconds: Int64?
        if values["lastFocusedAt"] is NSNull
            || values["lastFocusedAt"] == nil
        {
            lastFocusedAtMilliseconds = nil
        } else {
            lastFocusedAtMilliseconds = try integer(
                values["lastFocusedAt"],
                key: "lastFocusedAt",
                url: url
            )
        }
        return ClaudeDesktopRecord(
            desktopSessionID: desktopSessionID,
            cliSessionID: cliSessionID,
            cwd: try requiredString(values, key: "cwd", url: url),
            originCWD: try requiredString(
                values,
                key: "originCwd",
                url: url
            ),
            createdAtMilliseconds: try integer(
                values["createdAt"],
                key: "createdAt",
                url: url
            ),
            lastActivityAtMilliseconds: try integer(
                values["lastActivityAt"],
                key: "lastActivityAt",
                url: url
            ),
            lastFocusedAtMilliseconds: lastFocusedAtMilliseconds,
            isArchived: isArchived,
            title: title,
            groupScopeKey: [
                url.deletingLastPathComponent()
                    .deletingLastPathComponent().lastPathComponent,
                url.deletingLastPathComponent().lastPathComponent,
            ].joined(separator: "/")
        )
    }

    private static func fileStatus(
        _ url: URL
    ) throws -> (
        modifiedAtNanoseconds: Int64,
        size: Int64
    ) {
        var status = stat()
        let result = url.withUnsafeFileSystemRepresentation { path in
            Darwin.lstat(path, &status)
        }
        guard result == 0 else {
            throw ClaudeCodeProviderError.readFailed(
                "cannot stat \(url.path): \(String(cString: strerror(errno)))"
            )
        }
        let modifiedAtNanoseconds =
            Int64(status.st_mtimespec.tv_sec) * 1_000_000_000
            + Int64(status.st_mtimespec.tv_nsec)
        return (
            modifiedAtNanoseconds,
            Int64(status.st_size)
        )
    }

    private static func readTopLevelMetadata(
        _ url: URL
    ) throws -> [String: Any] {
        let data: Data
        do {
            let source = try FileHandle(forReadingFrom: url)
            defer { try? source.close() }
            data =
                try source.read(
                    upToCount: maximumMetadataPrefixBytes
                ) ?? Data()
        } catch {
            throw ClaudeCodeProviderError.invalidRecord(
                url,
                "cannot read record: \(error.localizedDescription)"
            )
        }
        guard let prefix = completeUTF8Prefix(data) else {
            throw ClaudeCodeProviderError.invalidRecord(
                url,
                "record must be valid UTF-8"
            )
        }
        do {
            var parser = MetadataPrefixParser(prefix)
            return try parser.parse()
        } catch let error as MetadataPrefixParser.Error {
            throw ClaudeCodeProviderError.invalidRecord(
                url,
                error.description
            )
        }
    }

    private static func completeUTF8Prefix(_ data: Data) -> String? {
        for trailingByteCount in 0...min(3, data.count) {
            let completeData = data.dropLast(trailingByteCount)
            if let value = String(data: completeData, encoding: .utf8) {
                return value
            }
        }
        return nil
    }

    private static func requiredString(
        _ values: [String: Any],
        key: String,
        url: URL
    ) throws -> String {
        guard let value = values[key] as? String, !value.isEmpty else {
            throw ClaudeCodeProviderError.invalidRecord(
                url,
                "\(key) must be a non-empty string"
            )
        }
        return value
    }

    private static func integer(
        _ value: Any?,
        key: String,
        url: URL
    ) throws -> Int64 {
        guard let number = value as? NSNumber,
            CFGetTypeID(number) != CFBooleanGetTypeID(),
            !["f", "d"].contains(String(cString: number.objCType)),
            let integer = Int64(exactly: number),
            integer >= 0
        else {
            throw ClaudeCodeProviderError.invalidRecord(
                url,
                "\(key) must be a non-negative integer"
            )
        }
        return integer
    }

    private static func selectedNativeSessionID(
        from records: [ClaudeDesktopRecord]
    ) -> String? {
        let focused = records.compactMap {
            record -> (String, Int64)? in
            guard let timestamp = record.lastFocusedAtMilliseconds else {
                return nil
            }
            return (record.desktopSessionID, timestamp)
        }
        guard let newest = focused.map(\.1).max() else {
            return nil
        }
        let matches = focused.filter { $0.1 == newest }
        return matches.count == 1 ? matches[0].0 : nil
    }
}

private struct ClaudeDesktopRecord {
    let desktopSessionID: String
    let cliSessionID: String
    let cwd: String
    let originCWD: String
    let createdAtMilliseconds: Int64
    let lastActivityAtMilliseconds: Int64
    let lastFocusedAtMilliseconds: Int64?
    let isArchived: Bool
    let title: String?
    let groupScopeKey: String
}

private struct ClaudeGroupScope {
    let groupIDs: [String]
    let assignments: [String: String]
    let order: [String: [String]]
}

private struct CachedClaudeRecord {
    let modifiedAtNanoseconds: Int64
    let size: Int64
    let record: ClaudeDesktopRecord
}

private struct MetadataPrefixParser {
    enum Error: Swift.Error, CustomStringConvertible {
        case message(String)

        var description: String {
            switch self {
            case .message(let message): message
            }
        }
    }

    private static let requiredFields: Set<String> = [
        "sessionId",
        "cliSessionId",
        "cwd",
        "originCwd",
        "createdAt",
        "lastActivityAt",
        "isArchived",
    ]
    private static let optionalFields: Set<String> = [
        "title",
        "lastFocusedAt",
    ]

    private let text: String
    private var index: String.Index

    init(_ text: String) {
        self.text = text
        index = text.startIndex
    }

    mutating func parse() throws -> [String: Any] {
        guard currentCharacter == "{" else {
            throw Error.message("record must be a JSON object")
        }
        advance()
        var values: [String: Any] = [:]
        while true {
            skipWhitespace()
            let key = try decodeJSONFragment() as? String
            guard let key else {
                throw Error.message(
                    "top-level field name must be a string"
                )
            }
            if Self.requiredFields.isSubset(of: values.keys)
                && !Self.optionalFields.contains(key)
            {
                break
            }
            skipWhitespace()
            guard currentCharacter == ":" else {
                throw Error.message("missing colon after \(key)")
            }
            advance()
            skipWhitespace()
            let value = try decodeJSONFragment()
            if Self.requiredFields.contains(key)
                || Self.optionalFields.contains(key)
            {
                values[key] = value
            }
            if Self.requiredFields.isSubset(of: values.keys)
                && Self.optionalFields.isSubset(of: values.keys)
            {
                break
            }
            skipWhitespace()
            guard let separator = currentCharacter,
                separator == "," || separator == "}"
            else {
                throw Error.message("invalid separator after \(key)")
            }
            if separator == "}" {
                break
            }
            advance()
        }
        let missing = Self.requiredFields.subtracting(values.keys).sorted()
        guard missing.isEmpty else {
            throw Error.message(
                "missing required fields: \(missing)"
            )
        }
        return values
    }

    private mutating func decodeJSONFragment() throws -> Any {
        let start = index
        try skipJSONValue()
        let fragment = String(text[start..<index])
        guard let data = fragment.data(using: .utf8) else {
            throw Error.message(
                "required metadata is invalid or exceeds 65536 bytes"
            )
        }
        do {
            return try JSONSerialization.jsonObject(
                with: data,
                options: [.fragmentsAllowed]
            )
        } catch {
            throw Error.message(
                "required metadata is invalid or exceeds 65536 bytes"
            )
        }
    }

    private mutating func skipJSONValue() throws {
        guard let character = currentCharacter else {
            throw invalidMetadata()
        }
        if character == "\"" {
            try skipString()
        } else if character == "{" || character == "[" {
            try skipContainer(opening: character)
        } else {
            while let currentCharacter,
                !currentCharacter.isWhitespace,
                currentCharacter != ",",
                currentCharacter != "}",
                currentCharacter != "]"
            {
                advance()
            }
            guard index > text.startIndex else {
                throw invalidMetadata()
            }
        }
    }

    private mutating func skipString() throws {
        advance()
        var escaped = false
        while let character = currentCharacter {
            advance()
            if escaped {
                escaped = false
            } else if character == "\\" {
                escaped = true
            } else if character == "\"" {
                return
            }
        }
        throw invalidMetadata()
    }

    private mutating func skipContainer(
        opening: Character
    ) throws {
        var stack: [Character] = [opening]
        advance()
        while let character = currentCharacter {
            if character == "\"" {
                try skipString()
                continue
            }
            if character == "{" || character == "[" {
                stack.append(character)
            } else if character == "}" || character == "]" {
                guard let expectedOpening = stack.last,
                    (expectedOpening == "{" && character == "}")
                        || (expectedOpening == "[" && character == "]")
                else {
                    throw invalidMetadata()
                }
                stack.removeLast()
                advance()
                if stack.isEmpty {
                    return
                }
                continue
            }
            advance()
        }
        throw invalidMetadata()
    }

    private mutating func skipWhitespace() {
        while currentCharacter?.isWhitespace == true {
            advance()
        }
    }

    private var currentCharacter: Character? {
        index < text.endIndex ? text[index] : nil
    }

    private mutating func advance() {
        index = text.index(after: index)
    }

    private func invalidMetadata() -> Error {
        Error.message(
            "required metadata is invalid or exceeds 65536 bytes"
        )
    }
}
