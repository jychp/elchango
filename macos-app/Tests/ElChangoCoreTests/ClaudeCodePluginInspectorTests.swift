import ElChangoCore
import Foundation
import Testing

@Suite("Claude Code plugin inspector")
struct ClaudeCodePluginInspectorTests {
    private func makeRegistryURL() -> URL {
        FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
            .appendingPathComponent("installed_plugins.json", isDirectory: false)
    }

    private func writeRegistry(_ contents: String, to url: URL) throws {
        try FileManager.default.createDirectory(
            at: url.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        try contents.write(to: url, atomically: true, encoding: .utf8)
    }

    private func registry(version: String) -> String {
        """
        {
          "version": 2,
          "plugins": {
            "elchango@elchango": [
              {
                "scope": "user",
                "version": "\(version)"
              }
            ]
          }
        }
        """
    }

    @Test("a missing CLI is reported conservatively")
    func cliNotAvailable() {
        let inspector = ClaudeCodePluginInspector(
            expectedVersion: "1.0.0",
            installedPluginsURL: makeRegistryURL()
        )
        #expect(inspector.classify(cliAvailable: false) == .cliNotAvailable)
    }

    @Test("an absent registry means the plugin is missing")
    func pluginMissingWhenRegistryAbsent() {
        let inspector = ClaudeCodePluginInspector(
            expectedVersion: "1.0.0",
            installedPluginsURL: makeRegistryURL()
        )
        let state = inspector.classify(cliAvailable: true)
        #expect(state == .pluginMissing)
        #expect(state.needsInstall)
        #expect(!state.needsUpdate)
    }

    @Test("an absent plugin key means the plugin is missing")
    func pluginMissingWhenKeyAbsent() throws {
        let url = makeRegistryURL()
        try writeRegistry(
            #"{"version": 2, "plugins": {"other@other": [{"version": "1.0.0"}]}}"#,
            to: url
        )
        defer { try? FileManager.default.removeItem(at: url) }

        let inspector = ClaudeCodePluginInspector(
            expectedVersion: "1.0.0",
            installedPluginsURL: url
        )
        #expect(inspector.classify(cliAvailable: true) == .pluginMissing)
    }

    @Test("equal versions match and offer no action")
    func matching() throws {
        let url = makeRegistryURL()
        try writeRegistry(registry(version: "1.0.0"), to: url)
        defer { try? FileManager.default.removeItem(at: url) }

        let inspector = ClaudeCodePluginInspector(
            expectedVersion: "1.0.0",
            installedPluginsURL: url
        )
        let state = inspector.classify(cliAvailable: true)
        #expect(state == .matching(version: "1.0.0"))
        #expect(!state.needsInstall)
        #expect(!state.needsUpdate)
    }

    @Test("different versions require an update")
    func mismatched() throws {
        let url = makeRegistryURL()
        try writeRegistry(registry(version: "0.9.0"), to: url)
        defer { try? FileManager.default.removeItem(at: url) }

        let inspector = ClaudeCodePluginInspector(
            expectedVersion: "1.0.0",
            installedPluginsURL: url
        )
        let state = inspector.classify(cliAvailable: true)
        #expect(state == .mismatched(installed: "0.9.0", expected: "1.0.0"))
        #expect(state.needsUpdate)
        #expect(!state.needsInstall)
    }

    @Test("invalid JSON is malformed")
    func malformedJSON() throws {
        let url = makeRegistryURL()
        try writeRegistry("not json", to: url)
        defer { try? FileManager.default.removeItem(at: url) }

        let inspector = ClaudeCodePluginInspector(
            expectedVersion: "1.0.0",
            installedPluginsURL: url
        )
        #expect(inspector.classify(cliAvailable: true) == .malformed)
    }

    @Test("a present key with no version is malformed")
    func malformedMissingVersion() throws {
        let url = makeRegistryURL()
        try writeRegistry(
            #"{"plugins": {"elchango@elchango": [{"scope": "user"}]}}"#,
            to: url
        )
        defer { try? FileManager.default.removeItem(at: url) }

        let inspector = ClaudeCodePluginInspector(
            expectedVersion: "1.0.0",
            installedPluginsURL: url
        )
        #expect(inspector.classify(cliAvailable: true) == .malformed)
    }

    @Test("conflicting versions are malformed")
    func malformedAmbiguousVersions() throws {
        let url = makeRegistryURL()
        try writeRegistry(
            """
            {"plugins": {"elchango@elchango": [
              {"scope": "user", "version": "1.0.0"},
              {"scope": "project", "version": "0.9.0"}
            ]}}
            """,
            to: url
        )
        defer { try? FileManager.default.removeItem(at: url) }

        let inspector = ClaudeCodePluginInspector(
            expectedVersion: "1.0.0",
            installedPluginsURL: url
        )
        #expect(inspector.classify(cliAvailable: true) == .malformed)
    }

    @Test("an unreadable registry is reported as unreadable")
    func unreadable() throws {
        // Point the registry path at a directory so reading it fails.
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(
            at: directory,
            withIntermediateDirectories: true
        )
        defer { try? FileManager.default.removeItem(at: directory) }

        let inspector = ClaudeCodePluginInspector(
            expectedVersion: "1.0.0",
            installedPluginsURL: directory
        )
        let state = inspector.classify(cliAvailable: true)
        guard case .unreadable = state else {
            Issue.record("expected unreadable, got \(state)")
            return
        }
    }

    @Test("install commands use the fixed application constants")
    func commandConstruction() {
        #expect(
            ClaudePluginInstaller.marketplaceAddArguments == [
                "plugin", "marketplace", "add", "jychp/elchango",
            ]
        )
        #expect(
            ClaudePluginInstaller.installArguments == [
                "plugin", "install", "elchango@elchango",
            ]
        )
    }

    @Test("the CLI locator returns an executable candidate only")
    func locatorSelectsExecutable() throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(
            at: directory,
            withIntermediateDirectories: true
        )
        defer { try? FileManager.default.removeItem(at: directory) }

        let missing = directory.appendingPathComponent("claude")
        let present = directory.appendingPathComponent("claude-real")
        FileManager.default.createFile(
            atPath: present.path,
            contents: Data("#!/bin/sh\n".utf8),
            attributes: [.posixPermissions: 0o755]
        )

        let locator = ClaudeCLILocator(candidateURLs: [missing, present])
        #expect(locator.locate() == present)

        let emptyLocator = ClaudeCLILocator(candidateURLs: [missing])
        #expect(emptyLocator.locate() == nil)
    }
}
