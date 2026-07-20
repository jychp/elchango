import ElChangoCore
import Foundation
import Testing

@Suite("Codex plugin installer")
struct CodexPluginInstallerTests {
    @Test("uses the official codex plugin command arguments")
    func officialArguments() {
        #expect(
            CodexPluginInstaller.marketplaceAddArguments == [
                "plugin", "marketplace", "add", "jychp/elchango",
            ])
        #expect(
            CodexPluginInstaller.marketplaceUpgradeArguments == [
                "plugin", "marketplace", "upgrade", "elchango",
            ])
        #expect(
            CodexPluginInstaller.installArguments == [
                "plugin", "add", "elchango@elchango",
            ])
    }

    @Test("locator picks the first executable candidate")
    func locatorFindsExecutable() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        let binary = root.appendingPathComponent("codex", isDirectory: false)
        try FileManager.default.createDirectory(
            at: root,
            withIntermediateDirectories: true
        )
        try Data("#!/bin/sh\n".utf8).write(to: binary)
        try FileManager.default.setAttributes(
            [.posixPermissions: 0o755],
            ofItemAtPath: binary.path
        )
        defer { try? FileManager.default.removeItem(at: root) }

        let missing = root.appendingPathComponent("absent", isDirectory: false)
        let locator = CodexCLILocator(candidateURLs: [missing, binary])
        #expect(locator.locate() == binary)
    }

    @Test("locator returns nil when no candidate is executable")
    func locatorMissing() {
        let missing = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: false)
        #expect(CodexCLILocator(candidateURLs: [missing]).locate() == nil)
    }

    @Test("a missing executable path yields a launch failure, never a crash")
    func installLaunchFailureIsHandled() async {
        let missing = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: false)
        let result = await CodexPluginInstaller().install(codexExecutable: missing)
        guard case .failure(let error) = result else {
            Issue.record("expected failure for a missing executable")
            return
        }
        #expect(error is CodexPluginInstaller.InstallError)
    }
}
