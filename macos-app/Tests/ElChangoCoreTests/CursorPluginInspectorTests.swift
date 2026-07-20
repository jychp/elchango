import ElChangoCore
import Foundation
import Testing

@Suite("Cursor plugin inspector")
struct CursorPluginInspectorTests {
    private func makeRootURL() -> URL {
        FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
            .appendingPathComponent("plugins", isDirectory: true)
    }

    private func writeManifest(
        version: String,
        toPluginDirectory directory: URL
    ) throws {
        let manifest =
            directory
            .appendingPathComponent(".cursor-plugin", isDirectory: true)
            .appendingPathComponent("plugin.json", isDirectory: false)
        try FileManager.default.createDirectory(
            at: manifest.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        try #"{"name": "elchango", "version": "\#(version)"}"#
            .write(to: manifest, atomically: true, encoding: .utf8)
    }

    private func cacheDirectory(
        root: URL,
        marketplace: String,
        ref: String
    ) -> URL {
        root
            .appendingPathComponent("cache", isDirectory: true)
            .appendingPathComponent(marketplace, isDirectory: true)
            .appendingPathComponent("elchango", isDirectory: true)
            .appendingPathComponent(ref, isDirectory: true)
    }

    @Test("missing Cursor is reported conservatively")
    func cursorNotDetected() {
        let inspector = CursorPluginInspector(
            expectedVersion: "1.0.0",
            pluginsRootURL: makeRootURL()
        )
        #expect(inspector.classify(cursorInstalled: false) == .cursorNotDetected)
    }

    @Test("no installation means the plugin is missing")
    func pluginMissing() throws {
        let root = makeRootURL()
        try FileManager.default.createDirectory(
            at: root,
            withIntermediateDirectories: true
        )
        defer { try? FileManager.default.removeItem(at: root) }

        let inspector = CursorPluginInspector(
            expectedVersion: "1.0.0",
            pluginsRootURL: root
        )
        let state = inspector.classify(cursorInstalled: true)
        #expect(state == .pluginMissing)
        #expect(state.needsInstall)
    }

    @Test("a local installation is reported as managed")
    func managed() throws {
        let root = makeRootURL()
        let local =
            root
            .appendingPathComponent("local", isDirectory: true)
            .appendingPathComponent("elchango", isDirectory: true)
        try writeManifest(version: "1.0.0", toPluginDirectory: local)
        defer { try? FileManager.default.removeItem(at: root) }

        let inspector = CursorPluginInspector(
            expectedVersion: "1.0.0",
            pluginsRootURL: root
        )
        let state = inspector.classify(cursorInstalled: true)
        #expect(state == .managed(version: "1.0.0"))
        #expect(!state.needsInstall)
        #expect(!state.needsUpdate)
    }

    @Test("equal Marketplace versions match")
    func matching() throws {
        let root = makeRootURL()
        let directory = cacheDirectory(
            root: root,
            marketplace: "elchango",
            ref: "abc123"
        )
        try writeManifest(version: "1.0.0", toPluginDirectory: directory)
        defer { try? FileManager.default.removeItem(at: root) }

        let inspector = CursorPluginInspector(
            expectedVersion: "1.0.0",
            pluginsRootURL: root
        )
        #expect(
            inspector.classify(cursorInstalled: true) == .matching(version: "1.0.0")
        )
    }

    @Test("different Marketplace versions require an update")
    func mismatched() throws {
        let root = makeRootURL()
        let directory = cacheDirectory(
            root: root,
            marketplace: "elchango",
            ref: "abc123"
        )
        try writeManifest(version: "0.9.0", toPluginDirectory: directory)
        defer { try? FileManager.default.removeItem(at: root) }

        let inspector = CursorPluginInspector(
            expectedVersion: "1.0.0",
            pluginsRootURL: root
        )
        let state = inspector.classify(cursorInstalled: true)
        #expect(state == .mismatched(installed: "0.9.0", expected: "1.0.0"))
        #expect(state.needsUpdate)
    }

    @Test("a manifest without a version is malformed")
    func malformed() throws {
        let root = makeRootURL()
        let directory = cacheDirectory(
            root: root,
            marketplace: "elchango",
            ref: "abc123"
        )
        let manifest =
            directory
            .appendingPathComponent(".cursor-plugin", isDirectory: true)
            .appendingPathComponent("plugin.json", isDirectory: false)
        try FileManager.default.createDirectory(
            at: manifest.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        try #"{"name": "elchango"}"#
            .write(to: manifest, atomically: true, encoding: .utf8)
        defer { try? FileManager.default.removeItem(at: root) }

        let inspector = CursorPluginInspector(
            expectedVersion: "1.0.0",
            pluginsRootURL: root
        )
        #expect(inspector.classify(cursorInstalled: true) == .malformed)
    }

    @Test("conflicting Marketplace versions are malformed")
    func malformedAmbiguous() throws {
        let root = makeRootURL()
        try writeManifest(
            version: "1.0.0",
            toPluginDirectory: cacheDirectory(
                root: root,
                marketplace: "elchango",
                ref: "abc123"
            )
        )
        try writeManifest(
            version: "0.9.0",
            toPluginDirectory: cacheDirectory(
                root: root,
                marketplace: "other",
                ref: "def456"
            )
        )
        defer { try? FileManager.default.removeItem(at: root) }

        let inspector = CursorPluginInspector(
            expectedVersion: "1.0.0",
            pluginsRootURL: root
        )
        #expect(inspector.classify(cursorInstalled: true) == .malformed)
    }
}
