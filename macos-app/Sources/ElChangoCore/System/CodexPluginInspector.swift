import Foundation

/// Result of comparing the installed Codex Desktop plugin with the version the
/// running elChango app expects. States are distinguished conservatively so the
/// app never acts on an installation it does not fully understand.
public enum CodexPluginState: Equatable, Sendable {
    /// Codex Desktop could not be detected on this machine.
    case codexNotDetected
    /// Codex is present but the elChango plugin is not installed.
    case pluginMissing
    /// The installed plugin version matches the version the app expects.
    case matching(version: String)
    /// The installed plugin version differs from the version the app expects.
    case mismatched(installed: String, expected: String)
    /// The installed manifest was found but is not a valid elChango manifest.
    case malformed
    /// The plugin is installed through a local-development location. elChango
    /// never touches such installations.
    case managed(version: String?)
    /// The installed manifest could not be read.
    case unreadable(String)

    /// Whether an install action would apply. The menu does not currently render
    /// a Codex action (the install path is not yet confirmed); the
    /// classification is provided for symmetry and for Diagnostics.
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

/// Reads Codex Desktop's plugin storage and classifies the elChango plugin
/// against the version the app expects, without modifying Codex state.
public protocol CodexPluginInspecting: Sendable {
    /// Classify the installed plugin. `codexInstalled` reports whether Codex
    /// Desktop itself was detected; when false, no action is meaningful.
    func classify(codexInstalled: Bool) -> CodexPluginState
}

/// Codex Desktop caches marketplace plugins under
/// `~/.codex/plugins/cache/<marketplace>/<plugin>/<ref>/.codex-plugin/plugin.json`,
/// the same shape Cursor uses. This inspector is read-only: the exact install
/// path for a local elChango plugin is not yet confirmed (see
/// `docs/providers/codex.md`), so no installer is provided.
public struct CodexPluginInspector: CodexPluginInspecting, Sendable {
    /// The directory name Codex uses for the elChango plugin.
    public static let pluginName = "elchango"

    /// A fragment every official manifest's `repository` or `homepage` must
    /// contain. This guards against an unrelated plugin that merely happens to
    /// be named `elchango`.
    public static let officialRepositoryFragment = "jychp/elchango"

    private let expectedVersion: String
    private let pluginsRootURL: URL

    public init(
        expectedVersion: String,
        pluginsRootURL: URL = CodexPluginInspector.defaultPluginsRootURL()
    ) {
        self.expectedVersion = expectedVersion
        self.pluginsRootURL = pluginsRootURL
    }

    /// Codex's plugin storage root, e.g. `~/.codex/plugins`.
    public static func defaultPluginsRootURL(
        fileManager: FileManager = .default
    ) -> URL {
        return fileManager.homeDirectoryForCurrentUser
            .appendingPathComponent(".codex", isDirectory: true)
            .appendingPathComponent("plugins", isDirectory: true)
    }

    public func classify(codexInstalled: Bool) -> CodexPluginState {
        guard codexInstalled else {
            return .codexNotDetected
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

        // A marketplace install lives under
        // `plugins/cache/<marketplace>/elchango/<ref>/.codex-plugin/plugin.json`.
        let manifests: [URL]
        do {
            manifests = try cacheManifestURLs()
        } catch {
            // An existing but inaccessible cache is unreadable, not missing.
            return .unreadable(error.localizedDescription)
        }

        var versions = Set<String>()
        for manifest in manifests {
            switch Self.readOfficialManifest(at: manifest) {
            case .version(let version):
                versions.insert(version)
            case .notOfficial:
                // A same-named but unofficial plugin is not our installation.
                continue
            case .malformed:
                return .malformed
            case .unreadable(let reason):
                return .unreadable(reason)
            }
        }

        guard let installedVersion = versions.first else {
            return .pluginMissing
        }
        guard versions.count == 1 else {
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

    /// Every `.codex-plugin/plugin.json` under
    /// `cache/<marketplace>/elchango/<ref>/`. Throws when a directory that does
    /// exist cannot be enumerated (e.g. permission denied), so the caller can
    /// distinguish an inaccessible cache from an absent one.
    private func cacheManifestURLs() throws -> [URL] {
        let fileManager = FileManager.default
        let cacheURL =
            pluginsRootURL
            .appendingPathComponent("cache", isDirectory: true)
        guard fileManager.fileExists(atPath: cacheURL.path) else {
            return []
        }
        let marketplaces = try fileManager.contentsOfDirectory(
            at: cacheURL,
            includingPropertiesForKeys: nil
        )
        var manifests: [URL] = []
        for marketplace in marketplaces {
            let pluginURL =
                marketplace
                .appendingPathComponent(Self.pluginName, isDirectory: true)
            var isDirectory: ObjCBool = false
            guard
                fileManager.fileExists(
                    atPath: pluginURL.path,
                    isDirectory: &isDirectory
                ),
                isDirectory.boolValue
            else {
                continue
            }
            let refs = try fileManager.contentsOfDirectory(
                at: pluginURL,
                includingPropertiesForKeys: nil
            )
            for ref in refs {
                let manifest =
                    ref
                    .appendingPathComponent(".codex-plugin", isDirectory: true)
                    .appendingPathComponent("plugin.json", isDirectory: false)
                if fileManager.fileExists(atPath: manifest.path) {
                    manifests.append(manifest)
                }
            }
        }
        return manifests
    }

    /// The outcome of reading a single cached manifest.
    private enum ManifestReading {
        case version(String)
        case notOfficial
        case malformed
        case unreadable(String)
    }

    /// Reads a cached manifest, returning its version only when the manifest's
    /// stable identity fields mark it as the official elChango plugin.
    private static func readOfficialManifest(at manifest: URL) -> ManifestReading {
        let data: Data
        do {
            data = try Data(contentsOf: manifest)
        } catch {
            return .unreadable(error.localizedDescription)
        }
        guard
            let object = try? JSONSerialization.jsonObject(with: data),
            let dictionary = object as? [String: Any]
        else {
            return .malformed
        }
        guard isOfficial(dictionary) else {
            return .notOfficial
        }
        guard
            let version = dictionary["version"] as? String,
            !version.isEmpty
        else {
            return .malformed
        }
        return .version(version)
    }

    /// Whether a manifest's name and repository/homepage identify it as the
    /// official elChango plugin rather than a same-named third-party plugin.
    private static func isOfficial(_ manifest: [String: Any]) -> Bool {
        guard (manifest["name"] as? String) == pluginName else {
            return false
        }
        let repository = (manifest["repository"] as? String) ?? ""
        let homepage = (manifest["homepage"] as? String) ?? ""
        return repository.contains(officialRepositoryFragment)
            || homepage.contains(officialRepositoryFragment)
    }

    /// Best-effort read of the `version` from a plugin directory's
    /// `.codex-plugin/plugin.json`.
    private static func readVersion(
        fromPluginDirectory directory: URL
    ) -> String? {
        let manifest =
            directory
            .appendingPathComponent(".codex-plugin", isDirectory: true)
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
