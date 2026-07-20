@preconcurrency import ApplicationServices
import Darwin
import ElChangoCore
import Foundation

public enum CodexProviderError: LocalizedError {
    case directoryNotFound(URL)
    case invalidRecord(URL, String)
    case readFailed(String)

    public var errorDescription: String? {
        switch self {
        case .directoryNotFound(let url):
            "Codex sessions directory not found: \(url.path)"
        case .invalidRecord(let url, let message):
            "\(url.path): \(message)"
        case .readFailed(let message):
            "Codex Desktop inventory read failed: \(message)"
        }
    }
}

/// Provider for the Codex desktop app shipped inside ChatGPT
/// (bundle `com.openai.codex`, URL scheme `codex://`). It reads the persistent
/// rollout files the desktop app writes under `~/.codex/sessions` and reports a
/// read-only inventory plus a conservative, rollout-derived state.
///
/// Scope: Codex Desktop sessions only. Rollouts authored by the Codex CLI and
/// subagent threads are excluded. Focus, new-session, and command dispatch are
/// implemented but fail closed: no live experiment has established a verifiable
/// exact-session target, so the descriptor declares no privileged capabilities
/// yet. Enabling a capability after live verification is a one-line change to
/// `descriptor` and `sessionCapabilities`.
public actor CodexProvider: AgentProvider {
    public nonisolated let descriptor = ProviderDescriptor(
        id: "codex",
        displayName: "Codex",
        icon: .codex,
        capabilities: []
    )

    public static let bundleID = "com.openai.codex"
    public static let desktopOriginator = "Codex Desktop"
    public static let desktopThreadSource = "user"
    public static let maximumMetadataPrefixBytes = 1_024 * 1_024
    public static let tailScanBytes = 256 * 1_024
    static let idleStateDetail =
        "Persistent Codex Desktop session; no live hook signal"
    public static let commands: Set<CommandID> = [
        .accept, .createPR, .commitPush, .compact,
    ]

    private let sessionsRootURL: URL
    private let sessionIndexURL: URL
    private let clock: @Sendable () -> Int64
    private let activityStore: CodexActivityStore
    private let automation: any NativeAutomating
    private let actionGate: PrivilegedActionGate
    private var recordCache: [URL: CachedCodexRecord] = [:]

    public init(
        sessionsRootURL: URL = CodexProvider.defaultSessionsRootURL,
        sessionIndexURL: URL = CodexProvider.defaultSessionIndexURL,
        activityStore: CodexActivityStore = CodexActivityStore(),
        automation: any NativeAutomating = NativeAutomation(),
        actionGate: PrivilegedActionGate = PrivilegedActionGate(),
        clock: @escaping @Sendable () -> Int64 = {
            Int64(Date().timeIntervalSince1970 * 1_000)
        }
    ) {
        self.sessionsRootURL = sessionsRootURL
        self.sessionIndexURL = sessionIndexURL
        self.activityStore = activityStore
        self.automation = automation
        self.actionGate = actionGate
        self.clock = clock
    }

    public static var defaultSessionsRootURL: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".codex/sessions", isDirectory: true)
    }

    public static var defaultSessionIndexURL: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".codex/session_index.jsonl")
    }

    public func snapshot() async throws -> ProviderSnapshot {
        do {
            let observedAtMilliseconds = clock()
            let titles = try sessionTitles()
            let records = try readDesktopRecords()
            // Deduplicate by native id, keeping the most recent rollout.
            var newest: [String: CodexSessionRecord] = [:]
            for record in records {
                if let existing = newest[record.nativeID],
                    existing.lastActivityAtMilliseconds
                        >= record.lastActivityAtMilliseconds
                {
                    continue
                }
                newest[record.nativeID] = record
            }
            var sessions: [AgentSession] = []
            for record in newest.values {
                let activity = activityStore.state(
                    for: record.nativeID,
                    observedAtMilliseconds: observedAtMilliseconds
                )
                sessions.append(
                    AgentSession(
                        providerID: descriptor.id,
                        nativeID: record.nativeID,
                        capabilities: Self.sessionCapabilities(),
                        icon: descriptor.icon,
                        title: titles[record.nativeID]
                            ?? "Untitled Codex session",
                        workspaceID: record.workspaceID,
                        workspacePath: record.cwd,
                        state: activity?.0 ?? .idle,
                        confidence: activity?.1 ?? .persisted,
                        stateDetail: activity?.2 ?? Self.idleStateDetail,
                        selected: false,
                        lastActivityAtMilliseconds:
                            record.lastActivityAtMilliseconds,
                        commands: []
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
                // No authoritative static selected-session signal exists for
                // Codex Desktop; selection stays unknown until live evidence.
                selectedNativeSessionID: nil,
                sessions: sessions,
                source: sessionsRootURL.path,
                readOnly: true
            )
        } catch let error as CodexProviderError {
            throw error
        } catch {
            throw CodexProviderError.readFailed(error.localizedDescription)
        }
    }

    public func isFrontmost() async throws -> Bool {
        await automation.frontmostBundleID() == Self.bundleID
    }

    // MARK: - Privileged actions (implemented, fail closed)

    public func focus(
        nativeSessionID: String
    ) async throws -> ProviderActionResult {
        try await actionGate.perform {
            try await self.performFocus(nativeSessionID: nativeSessionID)
        }
    }

    private func performFocus(
        nativeSessionID: String
    ) async throws -> ProviderActionResult {
        let records = try readDesktopRecords()
        guard records.contains(where: { $0.nativeID == nativeSessionID }) else {
            throw ProviderOperationError.targetUnverified(
                "unknown Codex Desktop session: \(nativeSessionID)"
            )
        }
        // No documented per-thread deep link and no static selected-session
        // signal exist, so an exact focus cannot be verified. Fail closed
        // rather than foreground the app on an unverifiable target.
        return ProviderActionResult(
            accepted: false,
            verdict: "FOCUS_NOT_VERIFIED_REQUIRES_LIVE",
            details: [
                "session_id": .string(nativeSessionID),
                "message": .string(
                    "Codex Desktop exposes no verifiable exact-session focus mechanism yet."
                ),
            ]
        )
    }

    public func openNew() async throws -> ProviderActionResult {
        try await actionGate.perform {
            try await self.performOpenNew()
        }
    }

    private func performOpenNew() async throws -> ProviderActionResult {
        // The `codex://` scheme is registered, but no documented neutral
        // new-session route is confirmed. Fail closed until a live experiment
        // proves an exact route that submits nothing.
        return ProviderActionResult(
            accepted: false,
            verdict: "NEW_SESSION_UNVERIFIED_REQUIRES_LIVE",
            details: [
                "message": .string(
                    "Codex Desktop exposes no confirmed neutral new-session route yet."
                )
            ]
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
        // Commands require an exact selected-session signal and a verified
        // agent prompt-input target, neither of which Codex Desktop exposes
        // statically. Fail closed.
        return ProviderActionResult(
            accepted: false,
            verdict: "TARGET_NOT_SELECTED",
            details: [
                "session_id": .string(nativeSessionID),
                "command_id": .string(commandID.rawValue),
                "message": .string(
                    "Codex Desktop exposes no verifiable command target yet."
                ),
            ]
        )
    }

    public func recordHook(
        _ payload: ProviderHookPayload,
        observedAtMilliseconds: Int64
    ) async throws -> ActivityObservation {
        try activityStore.record(
            payload,
            observedAtMilliseconds: observedAtMilliseconds
        )
    }

    // MARK: - Capabilities

    private static func sessionCapabilities() -> Set<ProviderCapability> {
        // Nothing is proven without a live experiment; report no privileged
        // per-session capabilities. State monitoring does not require any.
        []
    }

    // MARK: - Inventory

    private func readDesktopRecords() throws -> [CodexSessionRecord] {
        guard directoryExists(sessionsRootURL) else {
            throw CodexProviderError.directoryNotFound(sessionsRootURL)
        }
        let rolloutURLs = try rolloutFiles()
        let currentURLs = Set(rolloutURLs)
        recordCache = recordCache.filter { currentURLs.contains($0.key) }

        var records: [CodexSessionRecord] = []
        for url in rolloutURLs {
            let status = try Self.fileStatus(url)
            if let cached = recordCache[url],
                cached.modifiedAtNanoseconds == status.modifiedAtNanoseconds,
                cached.size == status.size
            {
                if let record = cached.record {
                    records.append(record)
                }
                continue
            }
            let record = try parseRollout(url, size: status.size)
            recordCache[url] = CachedCodexRecord(
                modifiedAtNanoseconds: status.modifiedAtNanoseconds,
                size: status.size,
                record: record
            )
            if let record {
                records.append(record)
            }
        }
        return records
    }

    /// Parse one rollout. Returns `nil` when the file is not a Codex Desktop
    /// user session (or is an unreadable artifact). Throws only when a record
    /// is confirmed to be a Codex Desktop session but its required fields are
    /// missing or malformed.
    private func parseRollout(
        _ url: URL,
        size: Int64
    ) throws -> CodexSessionRecord? {
        guard let payload = try Self.readSessionMeta(url) else {
            return nil
        }
        guard payload["originator"] as? String == Self.desktopOriginator,
            payload["thread_source"] as? String == Self.desktopThreadSource
        else {
            return nil
        }
        let nativeID = try Self.nativeID(payload, url: url)
        guard let cwd = payload["cwd"] as? String, !cwd.isEmpty else {
            throw CodexProviderError.invalidRecord(
                url,
                "session_meta cwd must be a non-empty string"
            )
        }
        var repositoryURL: String?
        if let git = payload["git"] as? [String: Any],
            let candidate = git["repository_url"] as? String,
            !candidate.isEmpty
        {
            repositoryURL = candidate
        }
        let metaTimestampMs = Self.parseISOMilliseconds(payload["timestamp"])
        let tail = try Self.readTail(url, size: size)
        let lastActivity =
            tail.lastEventAtMilliseconds ?? metaTimestampMs
            ?? Self.modifiedMilliseconds(url)
        return CodexSessionRecord(
            nativeID: nativeID,
            cwd: cwd,
            workspaceID: repositoryURL ?? cwd,
            lastActivityAtMilliseconds: lastActivity
        )
    }

    private func rolloutFiles() throws -> [URL] {
        guard
            let enumerator = FileManager.default.enumerator(
                at: sessionsRootURL,
                includingPropertiesForKeys: nil,
                options: [.skipsHiddenFiles]
            )
        else {
            throw CodexProviderError.readFailed(
                "cannot enumerate \(sessionsRootURL.path)"
            )
        }
        var urls: [URL] = []
        for case let url as URL in enumerator
        where url.lastPathComponent.hasPrefix("rollout-")
            && url.pathExtension == "jsonl"
        {
            urls.append(url)
        }
        return urls.sorted { $0.path < $1.path }
    }

    private func sessionTitles() throws -> [String: String] {
        guard FileManager.default.fileExists(atPath: sessionIndexURL.path)
        else {
            return [:]
        }
        let data: Data
        do {
            data = try Data(contentsOf: sessionIndexURL)
        } catch {
            throw CodexProviderError.readFailed(
                "cannot read session index: \(error.localizedDescription)"
            )
        }
        guard let text = String(data: data, encoding: .utf8) else {
            throw CodexProviderError.readFailed(
                "session index is not valid UTF-8"
            )
        }
        var titles: [String: String] = [:]
        for line in text.split(separator: "\n") {
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            guard !trimmed.isEmpty,
                let lineData = trimmed.data(using: .utf8),
                let record = try? JSONSerialization.jsonObject(with: lineData)
                    as? [String: Any],
                let id = record["id"] as? String,
                let name = record["thread_name"] as? String
            else {
                continue
            }
            titles[id] = name
        }
        return titles
    }

    private func directoryExists(_ url: URL) -> Bool {
        var isDirectory: ObjCBool = false
        return FileManager.default.fileExists(
            atPath: url.path,
            isDirectory: &isDirectory
        ) && isDirectory.boolValue
    }

    // MARK: - Bounded reads

    private static func readSessionMeta(
        _ url: URL
    ) throws -> [String: Any]? {
        let data: Data
        do {
            let handle = try FileHandle(forReadingFrom: url)
            defer { try? handle.close() }
            data = try handle.read(upToCount: maximumMetadataPrefixBytes) ?? Data()
        } catch {
            throw CodexProviderError.invalidRecord(
                url,
                "cannot read rollout: \(error.localizedDescription)"
            )
        }
        guard let newline = data.firstIndex(of: 0x0A) else {
            // No line terminator within the safe prefix: only fail if the file
            // is larger than the prefix (a genuinely oversized first line).
            if Int64(data.count) >= maximumMetadataPrefixBytes {
                throw CodexProviderError.invalidRecord(
                    url,
                    "session_meta line exceeds the safe prefix"
                )
            }
            return decodeMetaPayload(data, url: url)
        }
        return decodeMetaPayload(data[..<newline], url: url)
    }

    private static func decodeMetaPayload(
        _ slice: Data,
        url: URL
    ) -> [String: Any]? {
        let line = Data(slice)
        guard !line.isEmpty,
            let object = try? JSONSerialization.jsonObject(with: line)
                as? [String: Any]
        else {
            return nil
        }
        guard object["type"] as? String == "session_meta" else {
            return nil
        }
        return object["payload"] as? [String: Any]
    }

    private static func nativeID(
        _ payload: [String: Any],
        url: URL
    ) throws -> String {
        for key in ["session_id", "id"] {
            if let value = payload[key] as? String, !value.isEmpty {
                return value
            }
        }
        throw CodexProviderError.invalidRecord(
            url,
            "session_meta lacks a string session_id or id"
        )
    }

    private static func readTail(
        _ url: URL,
        size: Int64
    ) throws -> CodexTail {
        let data: Data
        do {
            let handle = try FileHandle(forReadingFrom: url)
            defer { try? handle.close() }
            if size > Int64(tailScanBytes) {
                try handle.seek(toOffset: UInt64(size) - UInt64(tailScanBytes))
            }
            data = try handle.readToEnd() ?? Data()
        } catch {
            throw CodexProviderError.readFailed(
                "cannot read rollout tail \(url.path): \(error.localizedDescription)"
            )
        }
        let text = String(decoding: data, as: UTF8.self)
        var lines = text.split(separator: "\n", omittingEmptySubsequences: true)
            .map(String.init)
        if size > Int64(tailScanBytes), !lines.isEmpty {
            lines.removeFirst()  // drop a possibly partial first line
        }
        var markerAtMilliseconds: Int64?
        for line in lines {
            guard let lineData = line.data(using: .utf8),
                let record = try? JSONSerialization.jsonObject(with: lineData)
                    as? [String: Any],
                record["type"] as? String == "event_msg",
                let payload = record["payload"] as? [String: Any],
                let eventType = payload["type"] as? String,
                CodexTail.lifecycleMarkers.contains(eventType)
            else {
                continue
            }
            markerAtMilliseconds = parseISOMilliseconds(record["timestamp"])
        }
        return CodexTail(lastEventAtMilliseconds: markerAtMilliseconds)
    }

    private static func fileStatus(
        _ url: URL
    ) throws -> (modifiedAtNanoseconds: Int64, size: Int64) {
        var status = stat()
        let result = url.withUnsafeFileSystemRepresentation { path in
            Darwin.lstat(path, &status)
        }
        guard result == 0 else {
            throw CodexProviderError.readFailed(
                "cannot stat \(url.path): \(String(cString: strerror(errno)))"
            )
        }
        let modifiedAtNanoseconds =
            Int64(status.st_mtimespec.tv_sec) * 1_000_000_000
            + Int64(status.st_mtimespec.tv_nsec)
        return (modifiedAtNanoseconds, Int64(status.st_size))
    }

    private static func modifiedMilliseconds(_ url: URL) -> Int64 {
        (try? fileStatus(url).modifiedAtNanoseconds).map { $0 / 1_000_000 } ?? 0
    }

    private static func parseISOMilliseconds(_ value: Any?) -> Int64? {
        guard let text = value as? String, !text.isEmpty else { return nil }
        // ISO8601DateFormatter is not Sendable, so build it locally. This runs
        // only for the few lifecycle-marker lines and the session_meta record.
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = formatter.date(from: text) {
            return Int64(date.timeIntervalSince1970 * 1_000)
        }
        formatter.formatOptions = [.withInternetDateTime]
        if let date = formatter.date(from: text) {
            return Int64(date.timeIntervalSince1970 * 1_000)
        }
        return nil
    }
}

private struct CodexSessionRecord {
    let nativeID: String
    let cwd: String
    let workspaceID: String
    let lastActivityAtMilliseconds: Int64
}

private struct CodexTail {
    // Live session state comes only from hook signals (the activity store);
    // the rollout tail is scanned solely to timestamp the last lifecycle event
    // for ordering, never to derive a state such as `done`.
    static let lifecycleMarkers: Set<String> = [
        "task_started", "task_complete", "turn_aborted",
    ]

    let lastEventAtMilliseconds: Int64?
}

private struct CachedCodexRecord {
    let modifiedAtNanoseconds: Int64
    let size: Int64
    let record: CodexSessionRecord?
}
