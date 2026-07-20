import ElChangoCore
import FlyingFox
import Foundation
import Testing

@Suite("Native foundation HTTP handler")
struct FoundationHTTPHandlerTests {
    private let authority = "127.0.0.1:8765"
    private let controlToken = "test-control-token"

    @Test("shipped default accepts every hook-capable provider route")
    func defaultHookProvidersIncludeAllShippedPlugins() {
        // Guards against a provider shipping a hook plugin whose /api/hooks/<id>
        // route is silently rejected by the production default allow-list.
        #expect(
            FoundationHTTPHandler.defaultAllowedHookProviderIDs
                == ["cursor", "claude-code", "codex"]
        )
    }

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
            health.unavailableProviders.isEmpty
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
        #expect(health.providers["cursor"] == nil)
        #expect(
            health.unavailableProviders["cursor"]
                == "fixture unavailable"
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
            headers: [
                .host: authority,
                .authorization: "Bearer \(controlToken)",
            ],
            body: Data()
        )

        let response = try await handler.handleRequest(invalid)

        #expect(response.statusCode == .badRequest)
    }

    @Test("API routes require the expected host and authentication")
    func apiAuthorization() async throws {
        let handler = try makeHandler(
            assetRoot: nil,
            accessibility: StubAccessibility(isTrusted: false)
        )
        let missingAuthentication = try await handler.handleRequest(
            HTTPRequest(
                method: .GET,
                version: .http11,
                path: "/api/snapshot",
                query: [],
                headers: [.host: authority],
                body: Data()
            )
        )
        let wrongHost = try await handler.handleRequest(
            request(
                path: "/api/snapshot",
                headers: [.host: "attacker.example"]
            )
        )
        let foreignOrigin = try await handler.handleRequest(
            request(
                path: "/api/snapshot",
                headers: [HTTPHeader("Origin"): "https://attacker.example"]
            )
        )

        #expect(missingAuthentication.statusCode == .unauthorized)
        #expect(wrongHost.statusCode == .misdirectedRequest)
        #expect(foreignOrigin.statusCode == .forbidden)
    }

    @Test("production handler accepts only a real loopback peer")
    func loopbackPeer() async throws {
        let handler = try makeHandler(
            assetRoot: nil,
            accessibility: StubAccessibility(isTrusted: false),
            requireLoopbackPeer: true
        )
        let missingPeer = try await handler.handleRequest(
            request(path: "/api/snapshot")
        )
        var loopback = request(path: "/api/snapshot")
        loopback.remoteAddress = .ip4("127.0.0.1", port: 54_321)
        let accepted = try await handler.handleRequest(loopback)
        var remote = request(path: "/api/snapshot")
        remote.remoteAddress = .ip4("192.0.2.10", port: 54_321)
        let rejected = try await handler.handleRequest(remote)

        #expect(missingPeer.statusCode == .forbidden)
        #expect(accepted.statusCode == .ok)
        #expect(rejected.statusCode == .forbidden)
    }

    @Test("web bootstrap is single-use and authorizes an HTTP-only cookie")
    func webBootstrap() async throws {
        let authorization = LoopbackAuthorization(
            controlToken: controlToken
        )
        let handler = try makeHandler(
            assetRoot: nil,
            accessibility: StubAccessibility(isTrusted: false),
            authorization: authorization
        )
        let bootstrap = await authorization.issueBootstrap()
        let bootstrapRequest = HTTPRequest(
            method: .GET,
            version: .http11,
            path: "/",
            query: [.init(name: "bootstrap", value: bootstrap)],
            headers: [.host: authority],
            body: Data()
        )

        let exchange = try await handler.handleRequest(bootstrapRequest)
        let setCookie = try #require(exchange.headers[.setCookie])
        let cookie =
            setCookie
            .split(separator: ";", maxSplits: 1)[0]
        let snapshot = try await handler.handleRequest(
            HTTPRequest(
                method: .GET,
                version: .http11,
                path: "/api/snapshot",
                query: [],
                headers: [
                    .host: authority,
                    .cookie: String(cookie),
                ],
                body: Data()
            )
        )
        let replay = try await handler.handleRequest(bootstrapRequest)

        #expect(exchange.statusCode == .seeOther)
        #expect(exchange.headers[.location] == "/")
        #expect(setCookie.contains("HttpOnly"))
        #expect(setCookie.contains("SameSite=Strict"))
        #expect(snapshot.statusCode == .ok)
        #expect(replay.statusCode == .unauthorized)
    }

    @Test("cookie-authenticated browser actions require the exact Origin")
    func browserActionOrigin() async throws {
        let authorization = LoopbackAuthorization(
            controlToken: controlToken
        )
        let handler = try makeHandler(
            assetRoot: nil,
            accessibility: StubAccessibility(isTrusted: false),
            authorization: authorization
        )
        let bootstrap = await authorization.issueBootstrap()
        let session = try #require(
            await authorization.consumeBootstrap(bootstrap)
        )
        let body = Data("{}".utf8)
        let baseHeaders: [HTTPHeader: String] = [
            .host: authority,
            .cookie: "elchango_session=\(session)",
            .contentType: "application/json",
            .contentLength: String(body.count),
        ]
        let missingOrigin = try await handler.handleRequest(
            HTTPRequest(
                method: .POST,
                version: .http11,
                path: "/api/activate",
                query: [],
                headers: baseHeaders,
                body: body
            )
        )
        var validHeaders = baseHeaders
        validHeaders[HTTPHeader("Origin")] = "http://\(authority)"
        let validOrigin = try await handler.handleRequest(
            HTTPRequest(
                method: .POST,
                version: .http11,
                path: "/api/activate",
                query: [],
                headers: validHeaders,
                body: body
            )
        )

        #expect(missingOrigin.statusCode == .forbidden)
        #expect(validOrigin.statusCode == .badRequest)
    }

    @Test("provider actions reject malformed requests")
    func actionRejected() async throws {
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

        #expect(response.statusCode == .badRequest)
        #expect(error.retryable == nil)
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
        let legacyFocus = try await handler.handleRequest(
            request(
                method: .POST,
                path: "/api/focus",
                headers: headers,
                body: Data("{}".utf8)
            )
        )
        let legacyIntent = try await handler.handleRequest(
            request(
                method: .POST,
                path: "/api/intent",
                headers: headers,
                body: Data("{}".utf8)
            )
        )

        #expect(hook.statusCode == .notFound)
        #expect(unknown.statusCode == .methodNotAllowed)
        #expect(legacyFocus.statusCode == .methodNotAllowed)
        #expect(legacyIntent.statusCode == .methodNotAllowed)
    }

    @Test("hook inventory failures return a structured unavailable response")
    func hookInventoryFailure() async throws {
        let handler = try makeHandler(
            assetRoot: nil,
            accessibility: StubAccessibility(isTrusted: true),
            providers: [ActionProvider(hookFails: true)]
        )
        let hookBody = try JSONEncoder().encode(
            ProviderHookPayload(
                hookEventName: "stop",
                conversationID: "target",
                status: "completed"
            )
        )

        let response = try await handler.handleRequest(
            request(
                method: .POST,
                path: "/api/hooks/test",
                headers: [
                    .contentType: "application/json",
                    .contentLength: String(hookBody.count),
                ],
                body: hookBody
            )
        )
        let error = try JSONDecoder().decode(
            APIErrorResponse.self,
            from: await responseBody(response)
        )

        #expect(response.statusCode == .serviceUnavailable)
        #expect(error.retryable == false)
    }

    @Test("hook endpoints reject browser origins and rate-limit providers")
    func hookBoundary() async throws {
        let limiter = HookRateLimiter(limit: 1, window: 60)
        let handler = try makeHandler(
            assetRoot: nil,
            accessibility: StubAccessibility(isTrusted: true),
            providers: [ActionProvider()],
            hookRateLimiter: limiter
        )
        let hookBody = try JSONEncoder().encode(
            ProviderHookPayload(
                hookEventName: "stop",
                conversationID: "target",
                status: "completed"
            )
        )
        let headers: [HTTPHeader: String] = [
            .contentType: "application/json",
            .contentLength: String(hookBody.count),
        ]
        let accepted = try await handler.handleRequest(
            request(
                method: .POST,
                path: "/api/hooks/test",
                headers: headers,
                body: hookBody
            )
        )
        let limited = try await handler.handleRequest(
            request(
                method: .POST,
                path: "/api/hooks/test",
                headers: headers,
                body: hookBody
            )
        )
        let unknownProvider = try await handler.handleRequest(
            request(
                method: .POST,
                path: "/api/hooks/unknown",
                headers: headers,
                body: hookBody
            )
        )
        var foreignHeaders = headers
        foreignHeaders[HTTPHeader("Origin")] = "https://attacker.example"
        let foreignOrigin = try await handler.handleRequest(
            request(
                method: .POST,
                path: "/api/hooks/other",
                headers: foreignHeaders,
                body: hookBody
            )
        )
        var loopbackOriginHeaders = headers
        loopbackOriginHeaders[HTTPHeader("Origin")] = "http://\(authority)"
        let loopbackOrigin = try await handler.handleRequest(
            request(
                method: .POST,
                path: "/api/hooks/test",
                headers: loopbackOriginHeaders,
                body: hookBody
            )
        )

        #expect(accepted.statusCode == .accepted)
        #expect(limited.statusCode == .tooManyRequests)
        #expect(unknownProvider.statusCode == .notFound)
        #expect(foreignOrigin.statusCode == .forbidden)
        #expect(loopbackOrigin.statusCode == .forbidden)
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
        let pickerSnapshot = try #require(picker.snapshot)
        let hardware = try await deckSnapshot(
            from: handler,
            clientID: "streamdeck"
        )

        #expect(longPress.statusCode == .ok)
        #expect(picker.action == .chooseSlotCommand)
        #expect(
            pickerSnapshot.buttons.contains {
                $0.commandID == .compact
            }
        )
        #expect(hardware.buttons[11].label == "Accept")

        let select = try await handler.handleRequest(
            try actionRequest(
                path: "/api/activate",
                clientID: "web",
                buttonID: "command-option:compact",
                revision: pickerSnapshot.revision
            )
        )
        let updated = try JSONDecoder().decode(
            DeckActivationResponse.self,
            from: await responseBody(select)
        )
        let updatedSnapshot = try #require(updated.snapshot)
        let shared = try await deckSnapshot(
            from: handler,
            clientID: "streamdeck"
        )

        #expect(select.statusCode == .ok)
        #expect(updatedSnapshot.buttons[11].label == "Compact")
        #expect(shared.buttons[11].label == "Compact")
    }

    @Test("native focus, command, launch, and hook routes dispatch")
    func providerActionRoutes() async throws {
        let handler = try makeHandler(
            assetRoot: nil,
            accessibility: StubAccessibility(isTrusted: true),
            providers: [ActionProvider()]
        )
        let initial = try await deckSnapshot(
            from: handler,
            clientID: "web"
        )
        let session = try #require(
            initial.buttons.first { $0.kind == .session }
        )
        let focus = try await handler.handleRequest(
            try actionRequest(
                path: "/api/activate",
                clientID: "web",
                buttonID: session.id,
                revision: initial.revision
            )
        )
        let command = try #require(
            initial.buttons.first {
                $0.action == .executeCommand && $0.enabled
            }
        )
        let commandResponse = try await handler.handleRequest(
            try actionRequest(
                path: "/api/activate",
                clientID: "web",
                buttonID: command.id,
                revision: initial.revision
            )
        )
        let newButton = try #require(
            initial.buttons.first { $0.id == "control:new" }
        )
        let chooseProvider = try await handler.handleRequest(
            try actionRequest(
                path: "/api/activate",
                clientID: "web",
                buttonID: newButton.id,
                revision: initial.revision
            )
        )
        let picker = try JSONDecoder().decode(
            DeckActivationResponse.self,
            from: await responseBody(chooseProvider)
        )
        let pickerSnapshot = try #require(picker.snapshot)
        let providerButton = try #require(
            pickerSnapshot.buttons.first {
                $0.action == .newSession
            }
        )
        let launch = try await handler.handleRequest(
            try actionRequest(
                path: "/api/activate",
                clientID: "web",
                buttonID: providerButton.id,
                revision: pickerSnapshot.revision
            )
        )
        let hookBody = try JSONEncoder().encode(
            ProviderHookPayload(
                hookEventName: "stop",
                conversationID: "target",
                status: "completed"
            )
        )
        let hook = try await handler.handleRequest(
            request(
                method: .POST,
                path: "/api/hooks/test",
                headers: [
                    .contentType: "application/json",
                    .contentLength: String(hookBody.count),
                ],
                body: hookBody
            )
        )

        #expect(focus.statusCode == .ok)
        #expect(commandResponse.statusCode == .ok)
        #expect(launch.statusCode == .ok)
        #expect(hook.statusCode == .accepted)
    }

    @Test("stale command revisions cannot retarget another session")
    func staleCommandTarget() async throws {
        let provider = ActionProvider()
        let handler = try makeHandler(
            assetRoot: nil,
            accessibility: StubAccessibility(isTrusted: true),
            providers: [provider]
        )
        let initial = try await deckSnapshot(
            from: handler,
            clientID: "web"
        )
        let command = try #require(
            initial.buttons.first {
                $0.action == .executeCommand && $0.enabled
            }
        )
        await provider.select("other")

        let response = try await handler.handleRequest(
            try actionRequest(
                path: "/api/activate",
                clientID: "web",
                buttonID: command.id,
                revision: initial.revision
            )
        )

        #expect(response.statusCode == .conflict)
        #expect(await provider.commandCount() == 0)
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
        var mergedHeaders: [HTTPHeader: String] = [
            .host: authority,
            .authorization: "Bearer \(controlToken)",
        ]
        mergedHeaders.merge(headers) { _, new in new }
        return HTTPRequest(
            method: method,
            version: .http11,
            path: path,
            query: [],
            headers: mergedHeaders,
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
                headers: [
                    .host: authority,
                    .authorization: "Bearer \(controlToken)",
                ],
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
        providers: [any AgentProvider] = [],
        authorization: LoopbackAuthorization? = nil,
        hookRateLimiter: HookRateLimiter = HookRateLimiter(),
        requireLoopbackPeer: Bool = false
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
            deckService: deckService,
            authorization: authorization
                ?? LoopbackAuthorization(
                    controlToken: controlToken
                ),
            hookRateLimiter: hookRateLimiter,
            expectedAuthority: authority,
            allowedHookProviderIDs: Set(
                providers.map { $0.descriptor.id }
            ).union(["cursor", "claude-code"]),
            requireLoopbackPeer: requireLoopbackPeer
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

private actor ActionProvider: AgentProvider {
    nonisolated let descriptor = ProviderDescriptor(
        id: "test",
        displayName: "Test",
        icon: .robot,
        capabilities: [.focusSession, .newSession, .executeCommand]
    )

    private var selectedID = "target"
    private var commandsExecuted = 0
    private let hookFails: Bool

    init(hookFails: Bool = false) {
        self.hookFails = hookFails
    }

    func snapshot() async throws -> ProviderSnapshot {
        ProviderSnapshot(
            providerID: "test",
            capabilities: descriptor.capabilities,
            observedAtMilliseconds: 100,
            selectedNativeSessionID: selectedID,
            sessions: ["target", "other"].map { sessionID in
                AgentSession(
                    providerID: "test",
                    nativeID: sessionID,
                    capabilities: descriptor.capabilities,
                    icon: .robot,
                    title: sessionID.capitalized,
                    workspaceID: "workspace",
                    workspacePath: "/tmp/workspace",
                    state: .idle,
                    confidence: .persisted,
                    stateDetail: "fixture",
                    selected: sessionID == selectedID,
                    lastActivityAtMilliseconds: 100,
                    commands: [
                        .accept, .commitPush, .createPR, .compact,
                    ]
                )
            },
            source: "fixture"
        )
    }

    func isFrontmost() async throws -> Bool {
        true
    }

    func focus(
        nativeSessionID: String
    ) async throws -> ProviderActionResult {
        acceptedResult("FOCUS_VERIFIED")
    }

    func openNew() async throws -> ProviderActionResult {
        acceptedResult("NEW_SESSION_REQUESTED")
    }

    func executeCommand(
        nativeSessionID: String,
        commandID: CommandID
    ) async throws -> ProviderActionResult {
        commandsExecuted += 1
        return acceptedResult("DISPATCH_VERIFIED")
    }

    func recordHook(
        _ payload: ProviderHookPayload,
        observedAtMilliseconds: Int64
    ) async throws -> ActivityObservation {
        if hookFails {
            throw FailingHookError.unavailable
        }
        return ActivityObservation(
            sessionID: payload.conversationID ?? "",
            event: payload.hookEventName ?? "",
            observedAtMilliseconds: observedAtMilliseconds,
            state: .done,
            confidence: .observed,
            detail: "fixture"
        )
    }

    func select(_ sessionID: String) {
        selectedID = sessionID
    }

    func commandCount() -> Int {
        commandsExecuted
    }

    private nonisolated func acceptedResult(
        _ verdict: String
    ) -> ProviderActionResult {
        ProviderActionResult(
            accepted: true,
            verdict: verdict,
            details: [
                "verdict": .string(verdict),
                "executed": .boolean(true),
            ]
        )
    }
}

private enum FailingHookError: LocalizedError {
    case unavailable

    var errorDescription: String? {
        "fixture hook inventory unavailable"
    }
}
