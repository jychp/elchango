import ElChangoCore
import Foundation
import Testing

@Suite("Stream Deck plugin inspector")
struct StreamDeckPluginInspectorTests {
    private func makeManifestURL() -> URL {
        FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
            .appendingPathComponent("manifest.json", isDirectory: false)
    }

    private func writeManifest(_ contents: String, to url: URL) throws {
        try FileManager.default.createDirectory(
            at: url.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        try contents.write(to: url, atomically: true, encoding: .utf8)
    }

    @Test("missing Stream Deck software is reported conservatively")
    func streamDeckNotDetected() {
        let inspector = StreamDeckPluginInspector(
            bundledVersion: "1.0.0.0",
            installedManifestURL: makeManifestURL()
        )
        #expect(
            inspector.classify(streamDeckInstalled: false)
                == .streamDeckNotDetected
        )
    }

    @Test("absent manifest means the plugin is missing")
    func pluginMissing() {
        let inspector = StreamDeckPluginInspector(
            bundledVersion: "1.0.0.0",
            installedManifestURL: makeManifestURL()
        )
        let state = inspector.classify(streamDeckInstalled: true)
        #expect(state == .pluginMissing)
        #expect(state.needsInstall)
        #expect(!state.needsUpdate)
    }

    @Test("equal versions match and offer no action")
    func matching() throws {
        let url = makeManifestURL()
        try writeManifest(
            #"{"UUID": "com.jychp.elchango", "Version": "1.0.0.0"}"#,
            to: url
        )
        defer { try? FileManager.default.removeItem(at: url) }

        let inspector = StreamDeckPluginInspector(
            bundledVersion: "1.0.0.0",
            installedManifestURL: url
        )
        let state = inspector.classify(streamDeckInstalled: true)
        #expect(state == .matching(version: "1.0.0.0"))
        #expect(!state.needsInstall)
        #expect(!state.needsUpdate)
    }

    @Test("different versions require an update")
    func mismatched() throws {
        let url = makeManifestURL()
        try writeManifest(
            #"{"UUID": "com.jychp.elchango", "Version": "0.9.0.0"}"#,
            to: url
        )
        defer { try? FileManager.default.removeItem(at: url) }

        let inspector = StreamDeckPluginInspector(
            bundledVersion: "1.0.0.0",
            installedManifestURL: url
        )
        let state = inspector.classify(streamDeckInstalled: true)
        #expect(
            state == .mismatched(installed: "0.9.0.0", bundled: "1.0.0.0")
        )
        #expect(state.needsUpdate)
        #expect(!state.needsInstall)
    }

    @Test("invalid JSON is malformed")
    func malformedJSON() throws {
        let url = makeManifestURL()
        try writeManifest("not json", to: url)
        defer { try? FileManager.default.removeItem(at: url) }

        let inspector = StreamDeckPluginInspector(
            bundledVersion: "1.0.0.0",
            installedManifestURL: url
        )
        #expect(inspector.classify(streamDeckInstalled: true) == .malformed)
    }

    @Test("a foreign UUID is malformed")
    func malformedUUID() throws {
        let url = makeManifestURL()
        try writeManifest(
            #"{"UUID": "com.example.other", "Version": "1.0.0.0"}"#,
            to: url
        )
        defer { try? FileManager.default.removeItem(at: url) }

        let inspector = StreamDeckPluginInspector(
            bundledVersion: "1.0.0.0",
            installedManifestURL: url
        )
        #expect(inspector.classify(streamDeckInstalled: true) == .malformed)
    }

    @Test("a missing version is malformed")
    func malformedMissingVersion() throws {
        let url = makeManifestURL()
        try writeManifest(#"{"UUID": "com.jychp.elchango"}"#, to: url)
        defer { try? FileManager.default.removeItem(at: url) }

        let inspector = StreamDeckPluginInspector(
            bundledVersion: "1.0.0.0",
            installedManifestURL: url
        )
        #expect(inspector.classify(streamDeckInstalled: true) == .malformed)
    }

    @Test("an unreadable manifest is reported as unreadable")
    func unreadable() throws {
        // Point the manifest path at a directory so reading it fails.
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(
            at: directory,
            withIntermediateDirectories: true
        )
        defer { try? FileManager.default.removeItem(at: directory) }

        let inspector = StreamDeckPluginInspector(
            bundledVersion: "1.0.0.0",
            installedManifestURL: directory
        )
        let state = inspector.classify(streamDeckInstalled: true)
        guard case .unreadable = state else {
            Issue.record("expected unreadable, got \(state)")
            return
        }
    }
}
