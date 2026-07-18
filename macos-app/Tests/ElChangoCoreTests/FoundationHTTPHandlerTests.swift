import ElChangoCore
import FlyingFox
import Foundation
import Testing

@Suite("Native foundation HTTP handler")
struct FoundationHTTPHandlerTests {
    @Test("health exposes degradation without blocking the host")
    func health() async throws {
        let handler = try makeHandler(
            assetRoot: nil,
            accessibility: StubAccessibility(isTrusted: false)
        )

        let response = try await handler.handleRequest(
            request(path: "/api/health")
        )
        let body = try await response.bodyData
        let health = try JSONDecoder().decode(HealthResponse.self, from: body)

        #expect(response.statusCode == .ok)
        #expect(health.status == "ok")
        #expect(health.providers.isEmpty)
        #expect(
            health.unavailableProviders.keys.sorted() == ["claude-code"]
        )
        #expect(!health.accessibilityTrusted)
        #expect(response.headers[HTTPHeader("Cache-Control")] == "no-store")
    }

    @Test("one provider failure remains visible without disabling the host")
    func providerDegradation() async throws {
        let handler = try makeHandler(
            assetRoot: nil,
            accessibility: StubAccessibility(isTrusted: false),
            providers: [FailingCursorProvider()]
        )

        let response = try await handler.handleRequest(
            request(path: "/api/health")
        )
        let health = try JSONDecoder().decode(
            HealthResponse.self,
            from: await responseBody(response)
        )

        #expect(response.statusCode == .ok)
        #expect(health.providers["cursor"] == [])
        #expect(
            health.unavailableProviders["cursor"]
                == "fixture unavailable"
        )
        #expect(
            health.unavailableProviders["claude-code"]
                == "provider not migrated to native host"
        )
    }

    @Test("snapshot remains compatible with both deck surfaces")
    func snapshot() async throws {
        let handler = try makeHandler(
            assetRoot: nil,
            accessibility: StubAccessibility(isTrusted: true)
        )

        let response = try await handler.handleRequest(
            request(path: "/api/snapshot")
        )
        let body = try await response.bodyData
        let snapshot = try JSONDecoder().decode(
            DeckSnapshot.self,
            from: body
        )

        #expect(response.statusCode == .ok)
        #expect(snapshot.buttons.count == 15)
        #expect(snapshot.readOnly)
        #expect(snapshot.selectedSessionID == nil)
    }

    @Test("snapshot rejects invalid client identifiers")
    func invalidClientID() async throws {
        let handler = try makeHandler(
            assetRoot: nil,
            accessibility: StubAccessibility(isTrusted: false)
        )
        let invalid = HTTPRequest(
            method: .GET,
            version: .http11,
            path: "/api/snapshot",
            query: [.init(name: "client_id", value: "has space")],
            headers: [:],
            body: Data()
        )

        let response = try await handler.handleRequest(invalid)

        #expect(response.statusCode == .badRequest)
    }

    @Test("provider actions fail closed")
    func actionRejected() async throws {
        let handler = try makeHandler(
            assetRoot: nil,
            accessibility: StubAccessibility(isTrusted: false)
        )
        let response = try await handler.handleRequest(
            request(
                method: .POST,
                path: "/api/focus",
                headers: [
                    .contentType: "application/json",
                    .contentLength: "2",
                ],
                body: Data("{}".utf8)
            )
        )
        let body = try await response.bodyData
        let error = try JSONDecoder().decode(
            APIErrorResponse.self,
            from: body
        )

        #expect(response.statusCode == .serviceUnavailable)
        #expect(error.retryable == false)
    }

    @Test("oversized action bodies are rejected before dispatch")
    func oversizedActionRejected() async throws {
        let handler = try makeHandler(
            assetRoot: nil,
            accessibility: StubAccessibility(isTrusted: false)
        )
        let response = try await handler.handleRequest(
            request(
                method: .POST,
                path: "/api/activate",
                headers: [
                    .contentType: "application/json",
                    .contentLength: "4097",
                ]
            )
        )

        #expect(response.statusCode == .payloadTooLarge)
    }

    @Test("hooks and unknown actions preserve fail-closed status semantics")
    func unavailableRoutes() async throws {
        let handler = try makeHandler(
            assetRoot: nil,
            accessibility: StubAccessibility(isTrusted: false)
        )
        let headers: [HTTPHeader: String] = [
            .contentType: "application/json",
            .contentLength: "2",
        ]
        let hook = try await handler.handleRequest(
            request(
                method: .POST,
                path: "/api/hooks/cursor",
                headers: headers,
                body: Data("{}".utf8)
            )
        )
        let unknown = try await handler.handleRequest(
            request(
                method: .POST,
                path: "/api/action",
                headers: headers,
                body: Data("{}".utf8)
            )
        )

        #expect(hook.statusCode == .notFound)
        #expect(unknown.statusCode == .methodNotAllowed)
    }

    @Test("long press customization is client-scoped and persists")
    func customizationFlow() async throws {
        let handler = try makeHandler(
            assetRoot: nil,
            accessibility: StubAccessibility(isTrusted: false)
        )
        let initial = try await deckSnapshot(
            from: handler,
            clientID: "web"
        )
        let longPress = try await handler.handleRequest(
            try actionRequest(
                path: "/api/long-press",
                clientID: "web",
                buttonID: "command:11:accept",
                revision: initial.revision
            )
        )
        let picker = try JSONDecoder().decode(
            DeckActivationResponse.self,
            from: await responseBody(longPress)
        )
        let hardware = try await deckSnapshot(
            from: handler,
            clientID: "streamdeck"
        )

        #expect(longPress.statusCode == .ok)
        #expect(picker.action == .chooseSlotCommand)
        #expect(
            picker.snapshot.buttons.contains {
                $0.commandID == .compact
            }
        )
        #expect(hardware.buttons[11].label == "Accept")

        let select = try await handler.handleRequest(
            try actionRequest(
                path: "/api/activate",
                clientID: "web",
                buttonID: "command-option:compact",
                revision: picker.snapshot.revision
            )
        )
        let updated = try JSONDecoder().decode(
            DeckActivationResponse.self,
            from: await responseBody(select)
        )
        let shared = try await deckSnapshot(
            from: handler,
            clientID: "streamdeck"
        )

        #expect(select.statusCode == .ok)
        #expect(updated.snapshot.buttons[11].label == "Compact")
        #expect(shared.buttons[11].label == "Compact")
    }

    @Test("assets use SPA fallback and reject traversal")
    func assetsAndTraversal() async throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(
            at: root,
            withIntermediateDirectories: true
        )
        defer { try? FileManager.default.removeItem(at: root) }
        try Data("<html>deck</html>".utf8).write(
            to: root.appendingPathComponent("index.html")
        )
        try Data("console.log('deck')".utf8).write(
            to: root.appendingPathComponent("app.123.js")
        )

        let handler = try makeHandler(
            assetRoot: root,
            accessibility: StubAccessibility(isTrusted: false)
        )
        let index = try await handler.handleRequest(request(path: "/"))
        let fallback = try await handler.handleRequest(
            request(path: "/sessions/cursor")
        )
        let asset = try await handler.handleRequest(
            request(path: "/app.123.js")
        )
        let traversal = try await handler.handleRequest(
            request(path: "/../secret")
        )

        #expect(index.statusCode == .ok)
        #expect(fallback.statusCode == .ok)
        #expect(asset.statusCode == .ok)
        #expect(traversal.statusCode == .badRequest)
        #expect(
            asset.headers[HTTPHeader("Cache-Control")]
                == "public, max-age=31536000, immutable"
        )
        #expect(index.headers[HTTPHeader("X-Content-Type-Options")] == "nosniff")
    }

    private func request(
        method: HTTPMethod = .GET,
        path: String,
        headers: [HTTPHeader: String] = [:],
        body: Data = Data()
    ) -> HTTPRequest {
        HTTPRequest(
            method: method,
            version: .http11,
            path: path,
            query: [],
            headers: headers,
            body: body
        )
    }

    private func actionRequest(
        path: String,
        clientID: String,
        buttonID: String,
        revision: Int
    ) throws -> HTTPRequest {
        let body = try JSONEncoder().encode(
            DeckActionRequest(
                clientID: clientID,
                buttonID: buttonID,
                revision: revision
            )
        )
        return request(
            method: .POST,
            path: path,
            headers: [
                .contentType: "application/json",
                .contentLength: String(body.count),
            ],
            body: body
        )
    }

    private func deckSnapshot(
        from handler: FoundationHTTPHandler,
        clientID: String
    ) async throws -> DeckSnapshot {
        let response = try await handler.handleRequest(
            HTTPRequest(
                method: .GET,
                version: .http11,
                path: "/api/snapshot",
                query: [.init(name: "client_id", value: clientID)],
                headers: [:],
                body: Data()
            )
        )
        return try JSONDecoder().decode(
            DeckSnapshot.self,
            from: await responseBody(response)
        )
    }

    private func responseBody(_ response: HTTPResponse) async -> Data {
        (try? await response.bodyData) ?? Data()
    }

    private func makeHandler(
        assetRoot: URL?,
        accessibility: any AccessibilityChecking,
        providers: [any AgentProvider] = []
    ) throws -> FoundationHTTPHandler {
        let preferences = try PreferencesStore(
            url: FileManager.default.temporaryDirectory
                .appendingPathComponent(UUID().uuidString)
                .appendingPathComponent("preferences.json")
        )
        let deckService = try DeckService(
            providers: providers,
            preferences: preferences
        )
        return FoundationHTTPHandler(
            assetRoot: assetRoot,
            accessibility: accessibility,
            deckService: deckService
        )
    }
}

private struct StubAccessibility: AccessibilityChecking {
    let isTrusted: Bool
}

private struct FailingCursorProvider: AgentProvider {
    let descriptor = ProviderDescriptor(
        id: "cursor",
        displayName: "Cursor",
        icon: .cursor,
        capabilities: []
    )

    func snapshot() async throws -> ProviderSnapshot {
        throw FailingCursorError.unavailable
    }

    func isFrontmost() async throws -> Bool {
        false
    }
}

private enum FailingCursorError: LocalizedError {
    case unavailable

    var errorDescription: String? {
        "fixture unavailable"
    }
}
