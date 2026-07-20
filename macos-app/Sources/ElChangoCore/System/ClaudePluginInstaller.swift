import Foundation

/// Locates the `claude` command-line executable. A GUI app inherits a minimal
/// `PATH`, so a fixed list of well-known install locations is checked in
/// addition to any directories the process `PATH` does provide.
public struct ClaudeCLILocator: Sendable {
    private let candidateURLs: [URL]

    public init(candidateURLs: [URL] = ClaudeCLILocator.defaultCandidateURLs()) {
        self.candidateURLs = candidateURLs
    }

    /// Well-known locations for the `claude` executable plus every directory in
    /// the process `PATH`.
    public static func defaultCandidateURLs(
        fileManager: FileManager = .default,
        environment: [String: String] = ProcessInfo.processInfo.environment
    ) -> [URL] {
        var candidates: [URL] = [
            fileManager.homeDirectoryForCurrentUser
                .appendingPathComponent(".claude/local/claude", isDirectory: false),
            URL(fileURLWithPath: "/opt/homebrew/bin/claude"),
            URL(fileURLWithPath: "/usr/local/bin/claude"),
            URL(fileURLWithPath: "/usr/bin/claude"),
        ]
        if let path = environment["PATH"] {
            for directory in path.split(separator: ":") where !directory.isEmpty {
                candidates.append(
                    URL(fileURLWithPath: String(directory), isDirectory: true)
                        .appendingPathComponent("claude", isDirectory: false)
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

/// Invokes Claude Code's official plugin commands to install or update the
/// elChango plugin. The command, marketplace, and plugin identifiers are fixed
/// application constants passed as argument arrays: no shell is involved, so no
/// value is ever interpolated into a command line.
public struct ClaudePluginInstaller: Sendable {
    public enum InstallError: LocalizedError {
        case launchFailed(String)
        case commandFailed(arguments: [String], status: Int32, message: String)

        public var errorDescription: String? {
            switch self {
            case .launchFailed(let message):
                "could not launch the claude CLI: \(message)"
            case .commandFailed(let arguments, let status, let message):
                {
                    let command = arguments.joined(separator: " ")
                    let detail = message.isEmpty ? "" : ": \(message)"
                    return
                        "claude \(command) exited with status \(status)\(detail)"
                }()
            }
        }
    }

    /// Arguments for `claude plugin marketplace add jychp/elchango`.
    public static let marketplaceAddArguments = [
        "plugin", "marketplace", "add",
        ClaudeCodePluginInspector.marketplaceReference,
    ]

    /// Arguments for `claude plugin install elchango@elchango`.
    public static let installArguments = [
        "plugin", "install", ClaudeCodePluginInspector.pluginReference,
    ]

    public init() {}

    /// Adds the marketplace and installs (or updates) the plugin, running each
    /// command sequentially. Never throws; the result reports the first failure.
    public func install(
        claudeExecutable: URL
    ) async -> Result<Void, Error> {
        for arguments in [
            Self.marketplaceAddArguments,
            Self.installArguments,
        ] {
            do {
                try Self.run(
                    executable: claudeExecutable,
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
        process.standardOutput = Pipe()

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
