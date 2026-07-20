import Foundation

/// Locates the `codex` command-line executable. A GUI app inherits a minimal
/// `PATH`, so a fixed list of well-known install locations is checked in
/// addition to any directories the process `PATH` does provide.
public struct CodexCLILocator: Sendable {
    private let candidateURLs: [URL]

    public init(candidateURLs: [URL] = CodexCLILocator.defaultCandidateURLs()) {
        self.candidateURLs = candidateURLs
    }

    /// Well-known locations for the `codex` executable plus every directory in
    /// the process `PATH`.
    public static func defaultCandidateURLs(
        fileManager: FileManager = .default,
        environment: [String: String] = ProcessInfo.processInfo.environment
    ) -> [URL] {
        let home = fileManager.homeDirectoryForCurrentUser
        var candidates: [URL] = [
            home.appendingPathComponent(".bun/bin/codex", isDirectory: false),
            home.appendingPathComponent(".local/bin/codex", isDirectory: false),
            URL(fileURLWithPath: "/opt/homebrew/bin/codex"),
            URL(fileURLWithPath: "/usr/local/bin/codex"),
            URL(fileURLWithPath: "/usr/bin/codex"),
        ]
        if let path = environment["PATH"] {
            for directory in path.split(separator: ":") where !directory.isEmpty {
                candidates.append(
                    URL(fileURLWithPath: String(directory), isDirectory: true)
                        .appendingPathComponent("codex", isDirectory: false)
                )
            }
        }
        return candidates
    }

    /// The first candidate that exists and is executable, if any.
    public func locate(
        fileManager: FileManager = .default
    ) -> URL? {
        for url in candidateURLs
        where fileManager.isExecutableFile(atPath: url.path) {
            return url
        }
        return nil
    }
}

/// Invokes Codex's official plugin commands to install or update the elChango
/// plugin. The command, marketplace, and plugin identifiers are fixed
/// application constants passed as argument arrays: no shell is involved, so no
/// value is ever interpolated into a command line.
public struct CodexPluginInstaller: Sendable {
    public enum InstallError: LocalizedError {
        case launchFailed(String)
        case commandFailed(arguments: [String], status: Int32, message: String)

        public var errorDescription: String? {
            switch self {
            case .launchFailed(let message):
                "could not launch the codex CLI: \(message)"
            case .commandFailed(let arguments, let status, let message):
                {
                    let command = arguments.joined(separator: " ")
                    let detail = message.isEmpty ? "" : ": \(message)"
                    return
                        "codex \(command) exited with status \(status)\(detail)"
                }()
            }
        }
    }

    /// Arguments for `codex plugin marketplace add jychp/elchango`.
    public static let marketplaceAddArguments = [
        "plugin", "marketplace", "add",
        CodexPluginInspector.marketplaceReference,
    ]

    /// Arguments for `codex plugin marketplace upgrade elchango`.
    public static let marketplaceUpgradeArguments = [
        "plugin", "marketplace", "upgrade",
        CodexPluginInspector.marketplaceName,
    ]

    /// Arguments for `codex plugin add elchango@elchango`.
    public static let installArguments = [
        "plugin", "add", CodexPluginInspector.pluginReference,
    ]

    public init() {}

    /// Adds the marketplace, refreshes it from its source, and installs (or
    /// updates) the plugin. `add` is best-effort so an already-registered
    /// marketplace does not abort the update path; `upgrade` then refreshes the
    /// cached source so the newest version is installed rather than a stale one.
    /// Never throws; the result reports the first failure of a required step.
    public func install(
        codexExecutable: URL
    ) async -> Result<Void, Error> {
        // Ensure the marketplace exists. When it is already registered this
        // exits non-zero; that is expected on the update path, so ignore it and
        // let the required steps below surface a genuinely unavailable source.
        try? Self.run(
            executable: codexExecutable,
            arguments: Self.marketplaceAddArguments
        )
        for arguments in [
            Self.marketplaceUpgradeArguments,
            Self.installArguments,
        ] {
            do {
                try Self.run(
                    executable: codexExecutable,
                    arguments: arguments
                )
            } catch {
                return .failure(error)
            }
        }
        return .success(())
    }

    private static func run(
        executable: URL,
        arguments: [String]
    ) throws {
        let process = Process()
        process.executableURL = executable
        process.arguments = arguments
        let errorPipe = Pipe()
        process.standardError = errorPipe
        // Discard stdout: an unread pipe would deadlock the process once its
        // buffer fills, since nothing drains it before `waitUntilExit`.
        process.standardOutput = FileHandle.nullDevice

        do {
            try process.run()
        } catch {
            throw InstallError.launchFailed(error.localizedDescription)
        }
        let errorData = errorPipe.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()

        guard process.terminationStatus == 0 else {
            let message =
                String(data: errorData, encoding: .utf8)?
                .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            throw InstallError.commandFailed(
                arguments: arguments,
                status: process.terminationStatus,
                message: message
            )
        }
    }
}
