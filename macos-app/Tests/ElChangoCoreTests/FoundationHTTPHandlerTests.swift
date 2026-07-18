import ElChangoCore
import FlyingFox
import Foundation
import Testing

@Suite("Native foundation HTTP handler")
struct FoundationHTTPHandlerTests {
    @Test("health exposes degradation without blocking the host")
    func health() async throws {
        let handler = FoundationHTTPHandler(
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
        #expect(health.unavailableProviders.keys.sorted() == [
            "claude-code",
            "cursor",
        ])
        #expect(!health.accessibilityTrusted)
        #expect(response.headers[HTTPHeader("Cache-Control")] == "no-store")
    }

    @Test("snapshot remains compatible with both deck surfaces")
    func snapshot() async throws {
        let handler = FoundationHTTPHandler(
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
        let handler = FoundationHTTPHandler(
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
        let handler = FoundationHTTPHandler(
            assetRoot: nil,
            accessibility: StubAccessibility(isTrusted: false)
        )
        let response = try await handler.handleRequest(
            request(
                method: .POST,
                path: "/api/activate",
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
        let handler = FoundationHTTPHandler(
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
        let handler = FoundationHTTPHandler(
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

        let handler = FoundationHTTPHandler(
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
}

private struct StubAccessibility: AccessibilityChecking {
    let isTrusted: Bool
}
