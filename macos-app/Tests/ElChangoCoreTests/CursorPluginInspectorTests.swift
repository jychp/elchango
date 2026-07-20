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
        toPluginDirectory directory: URL,
        name: String = "elchango",
        repository: String = "https://github.com/jychp/elchango"
    ) throws {
        try writeRawManifest(
            #"{"name": "\#(name)", "version": "\#(version)", "repository": "\#(repository)"}"#,
            toPluginDirectory: directory
        )
    }

    private func writeRawManifest(
        _ contents: String,
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
        try contents.write(to: manifest, atomically: true, encoding: .utf8)
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

    @Test("an official manifest without a version is malformed")
    func malformed() throws {
        let root = makeRootURL()
        try writeRawManifest(
            #"{"name": "elchango", "repository": "https://github.com/jychp/elchango"}"#,
            toPluginDirectory: cacheDirectory(
                root: root,
                marketplace: "elchango",
                ref: "abc123"
            )
        )
        defer { try? FileManager.default.removeItem(at: root) }

        let inspector = CursorPluginInspector(
            expectedVersion: "1.0.0",
            pluginsRootURL: root
        )
        #expect(inspector.classify(cursorInstalled: true) == .malformed)
    }

    @Test("a same-named third-party plugin is not treated as installed")
    func foreignIdentityIsNotOurs() throws {
        let root = makeRootURL()
        // Correct name and matching version, but an unrelated repository.
        try writeManifest(
            version: "1.0.0",
            toPluginDirectory: cacheDirectory(
                root: root,
                marketplace: "acme",
                ref: "abc123"
            ),
            repository: "https://github.com/acme/elchango"
        )
        defer { try? FileManager.default.removeItem(at: root) }

        let inspector = CursorPluginInspector(
            expectedVersion: "1.0.0",
            pluginsRootURL: root
        )
        #expect(inspector.classify(cursorInstalled: true) == .pluginMissing)
    }

    @Test("an inaccessible cache is unreadable, not missing")
    func inaccessibleCacheIsUnreadable() throws {
        let root = makeRootURL()
        try FileManager.default.createDirectory(
            at: root,
            withIntermediateDirectories: true
        )
        // A `cache` that exists but is not a directory cannot be enumerated,
        // standing in for a permission-denied cache.
        let cache = root.appendingPathComponent("cache", isDirectory: false)
        try Data("x".utf8).write(to: cache)
        defer { try? FileManager.default.removeItem(at: root) }

        let inspector = CursorPluginInspector(
            expectedVersion: "1.0.0",
            pluginsRootURL: root
        )
        let state = inspector.classify(cursorInstalled: true)
        guard case .unreadable = state else {
            Issue.record("expected unreadable, got \(state)")
            return
        }
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
