import Foundation

/// Result of comparing the installed Stream Deck plugin with the copy bundled
/// inside the elChango app. States are distinguished conservatively so the app
/// only offers an action when it is confident one is needed and safe.
public enum StreamDeckPluginState: Equatable, Sendable {
    /// Stream Deck software could not be detected on this machine.
    case streamDeckNotDetected
    /// Stream Deck is present but the elChango plugin is not installed.
    case pluginMissing
    /// The installed plugin version matches the bundled plugin.
    case matching(version: String)
    /// The installed plugin version differs from the bundled plugin.
    case mismatched(installed: String, bundled: String)
    /// The installed manifest was found but is not a valid elChango manifest.
    case malformed
    /// The installed manifest could not be read.
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

/// Reads the installed Stream Deck plugin manifest and classifies it against
/// the plugin bundled in the app, without modifying Stream Deck state.
public protocol StreamDeckPluginInspecting: Sendable {
    /// Classify the installed plugin. `streamDeckInstalled` reports whether the
    /// Stream Deck application itself was detected; when false, no install or
    /// update action is meaningful because nothing can open the artifact.
    func classify(streamDeckInstalled: Bool) -> StreamDeckPluginState
}

public struct StreamDeckPluginInspector: StreamDeckPluginInspecting, Sendable {
    /// The UUID every valid elChango manifest must declare.
    public static let pluginUUID = "com.jychp.elchango"

    /// The `.sdPlugin` directory name Stream Deck uses for the plugin.
    public static let pluginDirectoryName = "com.jychp.elchango.sdPlugin"

    private let expectedUUID: String
    private let bundledVersion: String
    private let installedManifestURL: URL

    public init(
        bundledVersion: String,
        installedManifestURL: URL =
            StreamDeckPluginInspector
            .defaultInstalledManifestURL(),
        expectedUUID: String = StreamDeckPluginInspector.pluginUUID
    ) {
        self.bundledVersion = bundledVersion
        self.installedManifestURL = installedManifestURL
        self.expectedUUID = expectedUUID
    }

    /// The manifest Stream Deck writes for an installed elChango plugin, e.g.
    /// `~/Library/Application Support/com.elgato.StreamDeck/Plugins/`
    /// `com.jychp.elchango.sdPlugin/manifest.json`.
    public static func defaultInstalledManifestURL(
        fileManager: FileManager = .default
    ) -> URL {
        return fileManager.urls(
            for: .applicationSupportDirectory,
            in: .userDomainMask
        )[0]
        .appendingPathComponent("com.elgato.StreamDeck", isDirectory: true)
        .appendingPathComponent("Plugins", isDirectory: true)
        .appendingPathComponent(pluginDirectoryName, isDirectory: true)
        .appendingPathComponent("manifest.json", isDirectory: false)
    }

    public func classify(streamDeckInstalled: Bool) -> StreamDeckPluginState {
        guard streamDeckInstalled else {
            return .streamDeckNotDetected
        }
        if !FileManager.default.fileExists(atPath: installedManifestURL.path) {
            return .pluginMissing
        }

        let data: Data
        do {
            data = try Data(contentsOf: installedManifestURL)
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
            let manifest = object as? [String: Any],
            let uuid = manifest["UUID"] as? String,
            uuid == expectedUUID,
            let installedVersion = manifest["Version"] as? String,
            !installedVersion.isEmpty
        else {
            return .malformed
        }

        if installedVersion == bundledVersion {
            return .matching(version: installedVersion)
        }
        return .mismatched(
            installed: installedVersion,
            bundled: bundledVersion
        )
    }
}
