import ElChangoCore
import Foundation
import Testing

@Suite("Native preferences store")
struct PreferencesStoreTests {
    @Test("defaults persist atomically and reload")
    func defaultsPersistAndReload() async throws {
        let directory = try temporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let url =
            directory
            .appendingPathComponent("nested", isDirectory: true)
            .appendingPathComponent("preferences.json")
        let store = try PreferencesStore(url: url)

        #expect(
            await store.snapshot().actionSlots
                == [.accept, .commitPush, .createPR]
        )
        try await store.setSessionIcon(
            sessionID: "cursor:session-1",
            icon: .robot
        )
        try await store.setActionSlot(index: 0, commandID: .compact)

        let reloaded = try PreferencesStore(url: url)
        let preferences = await reloaded.snapshot()
        #expect(preferences.sessionIcons == ["cursor:session-1": .robot])
        #expect(
            preferences.actionSlots == [
                .compact,
                .commitPush,
                .createPR,
            ])

        let temporaryFiles = try FileManager.default.contentsOfDirectory(
            at: url.deletingLastPathComponent(),
            includingPropertiesForKeys: nil
        )
        .filter { $0.pathExtension == "tmp" }
        #expect(temporaryFiles.isEmpty)
    }

    @Test("loads the Python version 1 schema")
    func loadsPythonSchema() async throws {
        let directory = try temporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let url = directory.appendingPathComponent("preferences.json")
        try Data(
            contentsOf: preferencesFixtureURL("preferences.json")
        ).write(to: url)

        let store = try PreferencesStore(url: url)
        let preferences = await store.snapshot()

        #expect(
            preferences.sessionIcons == [
                "claude-code:session-1": .terminal,
                "cursor:session-1": .robot,
            ])
        #expect(
            preferences.actionSlots == [
                .compact,
                .commitPush,
                .createPR,
            ])

        try await store.setActionSlot(index: 0, commandID: .compact)
        #expect(
            try Data(contentsOf: url)
                == Data(
                    contentsOf: preferencesFixtureURL("preferences.json")
                )
        )
        let attributes = try FileManager.default.attributesOfItem(
            atPath: url.path
        )
        #expect(
            (attributes[.posixPermissions] as? NSNumber)?.intValue == 0o600
        )
    }

    @Test("writes Python-compatible Unicode escapes and duplicate slots")
    func pythonByteCompatibility() async throws {
        let directory = try temporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let url = directory.appendingPathComponent("preferences.json")
        let fixture = preferencesFixtureURL("preferences-unicode.json")
        try Data(contentsOf: fixture).write(to: url)
        let store = try PreferencesStore(url: url)
        let preferences = await store.snapshot()
        let sessionID = try #require(preferences.sessionIcons.keys.first)

        try await store.setSessionIcon(sessionID: sessionID, icon: .robot)

        #expect(try Data(contentsOf: url) == Data(contentsOf: fixture))
        #expect(preferences.actionSlots == [.accept, .accept, .compact])
    }

    @Test("unsupported schemas fail explicitly")
    func unsupportedSchema() throws {
        let directory = try temporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let url = directory.appendingPathComponent("preferences.json")
        try Data(
            """
            {
              "version": 99,
              "session_icons": {},
              "action_slots": []
            }
            """.utf8
        ).write(to: url)

        #expect(throws: PreferencesStoreError.self) {
            _ = try PreferencesStore(url: url)
        }
    }

    @Test("invalid icons and action slots fail closed")
    func invalidValues() throws {
        let directory = try temporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let url = directory.appendingPathComponent("preferences.json")
        try Data(
            """
            {
              "version": 1,
              "session_icons": {"cursor:session-1": "plus"},
              "action_slots": ["accept", "commit_push", "create_pr"]
            }
            """.utf8
        ).write(to: url)

        #expect(throws: PreferencesStoreError.self) {
            _ = try PreferencesStore(url: url)
        }
    }

    @Test("failed persistence preserves the in-memory snapshot")
    func failedWritePreservesState() async throws {
        let directory = try temporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let blockedParent = directory.appendingPathComponent("blocked")
        try Data("not a directory".utf8).write(to: blockedParent)
        let store = try PreferencesStore(
            url: blockedParent.appendingPathComponent("preferences.json")
        )

        await #expect(throws: PreferencesStoreError.self) {
            try await store.setSessionIcon(
                sessionID: "cursor:session-1",
                icon: .robot
            )
        }

        #expect(await store.snapshot() == .defaults)
    }

    private func temporaryDirectory() throws -> URL {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(
            at: url,
            withIntermediateDirectories: true
        )
        return url
    }

    private func preferencesFixtureURL(_ name: String) -> URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("contracts/preferences/v1")
            .appendingPathComponent(name)
    }
}
