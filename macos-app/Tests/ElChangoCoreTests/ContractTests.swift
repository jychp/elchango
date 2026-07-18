import ElChangoCore
import Foundation
import Testing

@Suite("Public HTTP contracts")
struct ContractTests {
    @Test("foundation snapshot matches the versioned fixture")
    func snapshotFixture() async throws {
        let preferences = try PreferencesStore(
            url: FileManager.default.temporaryDirectory
                .appendingPathComponent(UUID().uuidString)
                .appendingPathComponent("preferences.json")
        )
        let service = try DeckService(
            preferences: preferences,
            wallClockNow: {
                Date(timeIntervalSince1970: 1_700_000_000)
            }
        )
        let snapshot = try await service.snapshot()
        let encoded = try sortedJSON(snapshot)
        let fixture = try Data(
            contentsOf: fixtureURL("snapshot-empty.json")
        )

        #expect(try jsonObject(encoded) == jsonObject(fixture))
        #expect(snapshot.buttons.count == 15)
        #expect(snapshot.buttons.map(\.position) == Array(0..<15))
    }

    @Test("native health matches the versioned fixture")
    func healthFixture() throws {
        let health = HealthResponse(
            focusEnabled: false,
            launchEnabled: false,
            actionsEnabled: false,
            providers: [:],
            unavailableProviders: [
                "claude-code": "provider not migrated to native host",
            ],
            accessibilityTrusted: false
        )
        let encoded = try sortedJSON(health)
        let fixture = try Data(
            contentsOf: fixtureURL("health-native-foundation.json")
        )

        #expect(try jsonObject(encoded) == jsonObject(fixture))
    }

    @Test("unavailable action error matches the versioned fixture")
    func unavailableActionFixture() throws {
        let error = APIErrorResponse(
            error: "native provider actions are not available yet",
            retryable: false
        )
        let encoded = try sortedJSON(error)
        let fixture = try Data(
            contentsOf: fixtureURL("action-unavailable.json")
        )

        #expect(try jsonObject(encoded) == jsonObject(fixture))
    }

    private func sortedJSON<Value: Encodable>(
        _ value: Value
    ) throws -> Data {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        return try encoder.encode(value)
    }

    private func jsonObject(_ data: Data) throws -> NSDictionary {
        try #require(
            JSONSerialization.jsonObject(with: data) as? NSDictionary
        )
    }

    private func fixtureURL(_ name: String) -> URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("contracts/http/v1")
            .appendingPathComponent(name)
    }
}
