import Foundation

/// Result of comparing the installed Claude Code plugin with the version the
/// running elChango app expects. States are distinguished conservatively so the
/// app only offers an action when it is confident one is needed and safe.
public enum ClaudeCodePluginState: Equatable, Sendable {
    /// The elChango plugin is not installed.
    case pluginMissing
    /// The installed plugin version matches the version the app expects.
    case matching(version: String)
    /// The installed plugin version differs from the version the app expects.
    case mismatched(installed: String, expected: String)
    /// The installed-plugins registry was found but does not describe a valid
    /// elChango installation.
    case malformed
    /// The installed-plugins registry could not be read.
    case unreadable(String)

    /// Whether the app should offer to install the plugin.
    public var needsInstall: Bool {
        self == .pluginMissing
    }

    /// Whether the app should offer to update the plugin.
    public var needsUpdate: Bool {
        if case .mismatched = self {
            return true
        }
        return false
    }
}

/// Reads Claude Code's installed-plugins registry and classifies the elChango
/// plugin against the version the app expects, without modifying Claude state.
public protocol ClaudeCodePluginInspecting: Sendable {
    /// Classify the installed plugin by reading Claude Code's registry. CLI
    /// availability is intentionally not an input: it only gates whether the app
    /// offers to run the install flow, never the reported plugin state.
    func classify() -> ClaudeCodePluginState
}

public struct ClaudeCodePluginInspector: ClaudeCodePluginInspecting, Sendable {
    /// The registry key Claude Code uses for the elChango plugin, formed from
    /// the plugin name and its marketplace name.
    public static let pluginKey = "elchango@elchango"

    /// The GitHub marketplace reference passed to `claude plugin marketplace
    /// add`.
    public static let marketplaceReference = "jychp/elchango"

    /// The registered marketplace name passed to `claude plugin marketplace
    /// update`. It is the `name` declared in the marketplace manifest.
    public static let marketplaceName = "elchango"

    /// The plugin reference passed to `claude plugin install`.
    public static let pluginReference = "elchango@elchango"

    private let expectedVersion: String
    private let installedPluginsURL: URL
    private let pluginKey: String

    public init(
        expectedVersion: String,
        installedPluginsURL: URL =
            ClaudeCodePluginInspector.defaultInstalledPluginsURL(),
        pluginKey: String = ClaudeCodePluginInspector.pluginKey
    ) {
        self.expectedVersion = expectedVersion
        self.installedPluginsURL = installedPluginsURL
        self.pluginKey = pluginKey
    }

    /// The registry Claude Code writes for installed plugins, e.g.
    /// `~/.claude/plugins/installed_plugins.json`.
    public static func defaultInstalledPluginsURL(
        fileManager: FileManager = .default
    ) -> URL {
        return fileManager.homeDirectoryForCurrentUser
            .appendingPathComponent(".claude", isDirectory: true)
            .appendingPathComponent("plugins", isDirectory: true)
            .appendingPathComponent(
                "installed_plugins.json",
                isDirectory: false
            )
    }

    public func classify() -> ClaudeCodePluginState {
        if !FileManager.default.fileExists(atPath: installedPluginsURL.path) {
            return .pluginMissing
        }

        let data: Data
        do {
            data = try Data(contentsOf: installedPluginsURL)
        } catch {
            return .unreadable(error.localizedDescription)
        }

        let object: Any
        do {
            object = try JSONSerialization.jsonObject(with: data)
        } catch {
            return .malformed
        }

        guard
            let registry = object as? [String: Any],
            let plugins = registry["plugins"] as? [String: Any]
        else {
            return .malformed
        }

        // An absent key means the plugin was never installed. A present key with
        // no usable version, or multiple conflicting versions, is treated as
        // malformed rather than guessed at.
        guard let entry = plugins[pluginKey] else {
            return .pluginMissing
        }
        guard let records = entry as? [[String: Any]] else {
            return .malformed
        }
        let versions = Set(
            records.compactMap { record -> String? in
                guard
                    let version = record["version"] as? String,
                    !version.isEmpty
                else {
                    return nil
                }
                return version
            }
        )
        guard let installedVersion = versions.first, versions.count == 1 else {
            return .malformed
        }

        if installedVersion == expectedVersion {
            return .matching(version: installedVersion)
        }
        return .mismatched(
            installed: installedVersion,
            expected: expectedVersion
        )
    }
}
