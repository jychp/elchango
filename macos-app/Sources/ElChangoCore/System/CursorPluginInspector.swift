import Foundation

/// Result of comparing the installed Cursor plugin with the version the running
/// elChango app expects. States are distinguished conservatively so the app
/// never acts on an installation it does not fully understand.
public enum CursorPluginState: Equatable, Sendable {
    /// Cursor could not be detected on this machine.
    case cursorNotDetected
    /// Cursor is present but the elChango plugin is not installed.
    case pluginMissing
    /// The installed plugin version matches the version the app expects.
    case matching(version: String)
    /// The installed plugin version differs from the version the app expects.
    case mismatched(installed: String, expected: String)
    /// The installed manifest was found but is not a valid elChango manifest.
    case malformed
    /// The plugin is installed through a local-development symlink (or another
    /// user-managed location). elChango never touches such installations.
    case managed(version: String?)
    /// The installed manifest could not be read.
    case unreadable(String)

    /// Whether an install action would apply. The menu does not currently render
    /// a Cursor action, but the classification is provided for symmetry and for
    /// Diagnostics.
    public var needsInstall: Bool {
        self == .pluginMissing
    }

    /// Whether an update action would apply.
    public var needsUpdate: Bool {
        if case .mismatched = self {
            return true
        }
        return false
    }
}

/// Reads Cursor's plugin storage and classifies the elChango plugin against the
/// version the app expects, without modifying Cursor state.
public protocol CursorPluginInspecting: Sendable {
    /// Classify the installed plugin. `cursorInstalled` reports whether Cursor
    /// itself was detected; when false, no action is meaningful.
    func classify(cursorInstalled: Bool) -> CursorPluginState
}

public struct CursorPluginInspector: CursorPluginInspecting, Sendable {
    /// The directory name Cursor uses for the elChango plugin.
    public static let pluginName = "elchango"

    private let expectedVersion: String
    private let pluginsRootURL: URL

    public init(
        expectedVersion: String,
        pluginsRootURL: URL = CursorPluginInspector.defaultPluginsRootURL()
    ) {
        self.expectedVersion = expectedVersion
        self.pluginsRootURL = pluginsRootURL
    }

    /// Cursor's plugin storage root, e.g. `~/.cursor/plugins`.
    public static func defaultPluginsRootURL(
        fileManager: FileManager = .default
    ) -> URL {
        return fileManager.homeDirectoryForCurrentUser
            .appendingPathComponent(".cursor", isDirectory: true)
            .appendingPathComponent("plugins", isDirectory: true)
    }

    public func classify(cursorInstalled: Bool) -> CursorPluginState {
        guard cursorInstalled else {
            return .cursorNotDetected
        }

        // A local-development install (`plugins/local/elchango`) is managed by
        // the user; report it but never act on it.
        let localURL =
            pluginsRootURL
            .appendingPathComponent("local", isDirectory: true)
            .appendingPathComponent(Self.pluginName, isDirectory: true)
        if FileManager.default.fileExists(atPath: localURL.path) {
            return .managed(
                version: Self.readVersion(fromPluginDirectory: localURL)
            )
        }

        // A Marketplace install lives under
        // `plugins/cache/<marketplace>/elchango/<ref>/.cursor-plugin/plugin.json`.
        let manifests = cacheManifestURLs()
        guard !manifests.isEmpty else {
            return .pluginMissing
        }

        var versions = Set<String>()
        for manifest in manifests {
            let data: Data
            do {
                data = try Data(contentsOf: manifest)
            } catch {
                return .unreadable(error.localizedDescription)
            }
            guard
                let object = try? JSONSerialization.jsonObject(with: data),
                let dictionary = object as? [String: Any],
                let version = dictionary["version"] as? String,
                !version.isEmpty
            else {
                return .malformed
            }
            versions.insert(version)
        }

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

    /// Every `.cursor-plugin/plugin.json` under
    /// `cache/<marketplace>/elchango/<ref>/`.
    private func cacheManifestURLs() -> [URL] {
        let fileManager = FileManager.default
        let cacheURL =
            pluginsRootURL
            .appendingPathComponent("cache", isDirectory: true)
        guard
            let marketplaces = try? fileManager.contentsOfDirectory(
                at: cacheURL,
                includingPropertiesForKeys: nil
            )
        else {
            return []
        }
        var manifests: [URL] = []
        for marketplace in marketplaces {
            let pluginURL =
                marketplace
                .appendingPathComponent(Self.pluginName, isDirectory: true)
            guard
                let refs = try? fileManager.contentsOfDirectory(
                    at: pluginURL,
                    includingPropertiesForKeys: nil
                )
            else {
                continue
            }
            for ref in refs {
                let manifest =
                    ref
                    .appendingPathComponent(".cursor-plugin", isDirectory: true)
                    .appendingPathComponent("plugin.json", isDirectory: false)
                if fileManager.fileExists(atPath: manifest.path) {
                    manifests.append(manifest)
                }
            }
        }
        return manifests
    }

    /// Best-effort read of the `version` from a plugin directory's
    /// `.cursor-plugin/plugin.json`.
    private static func readVersion(
        fromPluginDirectory directory: URL
    ) -> String? {
        let manifest =
            directory
            .appendingPathComponent(".cursor-plugin", isDirectory: true)
            .appendingPathComponent("plugin.json", isDirectory: false)
        guard
            let data = try? Data(contentsOf: manifest),
            let object = try? JSONSerialization.jsonObject(with: data),
            let dictionary = object as? [String: Any],
            let version = dictionary["version"] as? String,
            !version.isEmpty
        else {
            return nil
        }
        return version
    }
}
