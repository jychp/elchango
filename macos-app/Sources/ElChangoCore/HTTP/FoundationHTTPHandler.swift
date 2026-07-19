import FlyingFox
import Foundation

public struct FoundationHTTPHandler: HTTPHandler {
    public static let actionBodyLimit = 4_096
    public static let hookBodyLimit = 65_536

    private let assetRoot: URL?
    private let accessibility: any AccessibilityChecking
    private let deckService: DeckService
    private let authorization: LoopbackAuthorization
    private let hookRateLimiter: HookRateLimiter
    private let expectedAuthority: String
    private let allowedOrigin: String
    private let allowedHookProviderIDs: Set<String>
    private let requireLoopbackPeer: Bool
    private let unavailableProviders: [String: String]
    private let encoder: JSONEncoder
    private let decoder: JSONDecoder

    public init(
        assetRoot: URL?,
        accessibility: any AccessibilityChecking,
        deckService: DeckService,
        authorization: LoopbackAuthorization,
        hookRateLimiter: HookRateLimiter = HookRateLimiter(),
        expectedAuthority: String = "127.0.0.1:8765",
        allowedHookProviderIDs: Set<String> = ["cursor", "claude-code"],
        requireLoopbackPeer: Bool = true,
        unavailableProviders: [String: String] = [:]
    ) {
        self.assetRoot = assetRoot?.standardizedFileURL
        self.accessibility = accessibility
        self.deckService = deckService
        self.authorization = authorization
        self.hookRateLimiter = hookRateLimiter
        self.expectedAuthority = expectedAuthority
        self.allowedOrigin = "http://\(expectedAuthority)"
        self.allowedHookProviderIDs = allowedHookProviderIDs
        self.requireLoopbackPeer = requireLoopbackPeer
        self.unavailableProviders = unavailableProviders
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        self.encoder = encoder
        self.decoder = JSONDecoder()
    }

    public func handleRequest(_ request: HTTPRequest) async throws -> HTTPResponse {
        guard !requireLoopbackPeer || Self.isLoopback(request.remoteAddress)
        else {
            return try jsonResponse(
                .forbidden,
                APIErrorResponse(error: "request peer is not loopback")
            )
        }
        guard
            request.headers[.host]?.lowercased()
                == expectedAuthority.lowercased()
        else {
            return try jsonResponse(
                .misdirectedRequest,
                APIErrorResponse(error: "unexpected Host header")
            )
        }

        if request.path.hasPrefix("/api/"),
            let origin = request.headers[HTTPHeader("Origin")],
            origin != allowedOrigin
        {
            return try jsonResponse(
                .forbidden,
                APIErrorResponse(error: "request Origin is not allowed")
            )
        }
        if request.path.hasPrefix("/api/hooks/"),
            request.headers[HTTPHeader("Origin")] != nil
        {
            return try jsonResponse(
                .forbidden,
                APIErrorResponse(error: "hook requests cannot have an Origin")
            )
        }

        if request.method == .GET {
            if request.path == "/",
                let bootstrap = request.query["bootstrap"]
            {
                return try await consumeBootstrap(bootstrap)
            }
            switch request.path {
            case "/api/health":
                guard await isAuthorized(request) else {
                    return try unauthorizedResponse()
                }
                let diagnostics = await deckService.providerDiagnostics()
                let unavailable = unavailableProviders.merging(
                    diagnostics.unavailableProviders
                ) { _, runtimeError in runtimeError }
                let capabilities = diagnostics.providers.values
                return try jsonResponse(
                    .ok,
                    HealthResponse(
                        focusEnabled: capabilities.contains {
                            $0.contains("focus_session")
                        },
                        launchEnabled: capabilities.contains {
                            $0.contains("new_session")
                        },
                        actionsEnabled: capabilities.contains {
                            $0.contains("execute_command")
                        },
                        providers: diagnostics.providers,
                        unavailableProviders: unavailable,
                        accessibilityTrusted: accessibility.isTrusted
                    )
                )
            case "/api/snapshot":
                guard await isAuthorized(request) else {
                    return try unauthorizedResponse()
                }
                let clientID = request.query["client_id"] ?? "web"
                do {
                    return try jsonResponse(
                        .ok,
                        await deckService.snapshot(clientID: clientID)
                    )
                } catch let error as DeckServiceError {
                    return try jsonResponse(
                        .badRequest,
                        APIErrorResponse(
                            error: error.localizedDescription
                        )
                    )
                }
            default:
                if request.path.hasPrefix("/api/") {
                    return try jsonResponse(
                        .notFound,
                        APIErrorResponse(error: "not found")
                    )
                }
                return serveAsset(path: request.path)
            }
        }

        if request.method == .POST {
            return try await handlePost(request)
        }

        return try jsonResponse(
            .methodNotAllowed,
            APIErrorResponse(error: "method not allowed")
        )
    }

    private func handlePost(
        _ request: HTTPRequest
    ) async throws -> HTTPResponse {
        let isHook = request.path.hasPrefix("/api/hooks/")
        let hookProviderID =
            isHook
            ? String(request.path.dropFirst("/api/hooks/".count))
            : nil
        if let hookProviderID,
            !allowedHookProviderIDs.contains(hookProviderID)
        {
            return try jsonResponse(
                .notFound,
                APIErrorResponse(error: "hook provider is not supported")
            )
        }
        if !isHook {
            guard let authorizationKind = await authorizationKind(request)
            else {
                return try unauthorizedResponse()
            }
            if authorizationKind == .cookie,
                request.headers[HTTPHeader("Origin")] != allowedOrigin
            {
                return try jsonResponse(
                    .forbidden,
                    APIErrorResponse(
                        error: "browser actions require the expected Origin"
                    )
                )
            }
        }
        let limit = isHook ? Self.hookBodyLimit : Self.actionBodyLimit
        guard isHook || Self.actionPaths.contains(request.path) else {
            return try jsonResponse(
                .methodNotAllowed,
                APIErrorResponse(
                    error: "actions are not enabled in the native foundation"
                )
            )
        }
        guard
            request.headers[.contentType]?.lowercased()
                .hasPrefix("application/json") == true
        else {
            return try jsonResponse(
                .unsupportedMediaType,
                APIErrorResponse(error: "Content-Type must be application/json")
            )
        }
        if let length = request.headers[.contentLength].flatMap(Int.init),
            length > limit
        {
            return try jsonResponse(
                .payloadTooLarge,
                APIErrorResponse(error: "request body is too large")
            )
        }
        let body = try await request.bodyData
        if body.count > limit {
            return try jsonResponse(
                .payloadTooLarge,
                APIErrorResponse(error: "request body is too large")
            )
        }
        guard !body.isEmpty else {
            return try jsonResponse(
                .badRequest,
                APIErrorResponse(error: "invalid payload size")
            )
        }
        if isHook {
            let providerID = hookProviderID ?? ""
            guard await hookRateLimiter.allow(providerID: providerID) else {
                return try jsonResponse(
                    .tooManyRequests,
                    APIErrorResponse(
                        error: "hook request rate limit exceeded",
                        retryable: true
                    )
                )
            }
            return try await receiveHook(
                providerID: providerID,
                body: body
            )
        }
        let payload: DeckActionRequest
        do {
            payload = try decoder.decode(
                DeckActionRequest.self,
                from: body
            )
            try DeckService.validateClientID(payload.clientID)
            guard !payload.buttonID.isEmpty else {
                throw DeckServiceError.invalidAction(
                    "button_id must be a non-empty string"
                )
            }
        } catch {
            return try jsonResponse(
                .badRequest,
                APIErrorResponse(error: error.localizedDescription)
            )
        }

        do {
            if request.path == "/api/long-press" {
                return try await longPress(payload)
            }
            return try await activate(payload)
        } catch let error as DeckServiceError {
            return try jsonResponse(
                .conflict,
                APIErrorResponse(error: error.localizedDescription)
            )
        } catch let error as PreferencesStoreError {
            return try jsonResponse(
                .serviceUnavailable,
                APIErrorResponse(
                    error: error.localizedDescription,
                    retryable: false
                )
            )
        } catch let error as ProviderOperationError {
            return try jsonResponse(
                .serviceUnavailable,
                APIErrorResponse(
                    error: error.localizedDescription,
                    retryable: false
                )
            )
        } catch {
            return try jsonResponse(
                .serviceUnavailable,
                APIErrorResponse(
                    error: error.localizedDescription,
                    retryable: false
                )
            )
        }
    }

    private func consumeBootstrap(
        _ bootstrap: String
    ) async throws -> HTTPResponse {
        guard let session = await authorization.consumeBootstrap(bootstrap)
        else {
            return try jsonResponse(
                .unauthorized,
                APIErrorResponse(error: "web bootstrap is invalid or expired")
            )
        }
        var headers = securityHeaders
        headers[.location] = "/"
        headers[.setCookie] = [
            "\(LoopbackAuthorization.webSessionCookieName)=\(session)",
            "HttpOnly",
            "SameSite=Strict",
            "Path=/",
            "Max-Age=43200",
        ].joined(separator: "; ")
        headers[HTTPHeader("Cache-Control")] = "no-store"
        return HTTPResponse(
            statusCode: .seeOther,
            headers: headers
        )
    }

    private func isAuthorized(_ request: HTTPRequest) async -> Bool {
        await authorizationKind(request) != nil
    }

    private func authorizationKind(
        _ request: HTTPRequest
    ) async -> AuthorizationKind? {
        if await authorization.acceptsBearer(
            request.headers[.authorization]
        ) {
            return .bearer
        }
        if await authorization.acceptsCookie(
            request.headers[.cookie]
        ) {
            return .cookie
        }
        return nil
    }

    private func unauthorizedResponse() throws -> HTTPResponse {
        var response = try jsonResponse(
            .unauthorized,
            APIErrorResponse(error: "authentication is required")
        )
        response.headers[HTTPHeader("WWW-Authenticate")] = "Bearer"
        return response
    }

    private func receiveHook(
        providerID: String,
        body: Data
    ) async throws -> HTTPResponse {
        let payload: ProviderHookPayload
        do {
            payload = try decoder.decode(
                ProviderHookPayload.self,
                from: body
            )
        } catch {
            return try jsonResponse(
                .badRequest,
                APIErrorResponse(error: error.localizedDescription)
            )
        }
        do {
            let observation = try await deckService.recordHook(
                providerID: providerID,
                payload: payload,
                observedAtMilliseconds: Int64(
                    Date().timeIntervalSince1970 * 1_000
                )
            )
            return try jsonResponse(
                .accepted,
                JSONValue.object([
                    "accepted": .boolean(true),
                    "provider_id": .string(providerID),
                    "session_id": .string(observation.sessionID),
                ])
            )
        } catch let error as ProviderOperationError {
            let status: HTTPStatusCode
            switch error {
            case .unsupported:
                status = .notFound
            case .invalidHook:
                status = .badRequest
            case .targetUnverified, .system:
                status = .serviceUnavailable
            }
            return try jsonResponse(
                status,
                APIErrorResponse(error: error.localizedDescription)
            )
        } catch {
            return try jsonResponse(
                .serviceUnavailable,
                APIErrorResponse(
                    error: error.localizedDescription,
                    retryable: false
                )
            )
        }
    }

    private func activate(
        _ request: DeckActionRequest
    ) async throws -> HTTPResponse {
        let snapshot = try await deckService.snapshot(
            clientID: request.clientID
        )
        guard
            let button = snapshot.buttons.first(
                where: { $0.id == request.buttonID && $0.enabled }
            )
        else {
            throw DeckServiceError.invalidAction(
                "button is not actionable in the current snapshot"
            )
        }
        if button.kind == .session, let sessionID = button.sessionID {
            let focus = try await deckService.focusSession(
                sessionID: sessionID
            )
            return try jsonResponse(
                focus.accepted ? .ok : .conflict,
                JSONValue.object([
                    "accepted": .boolean(focus.accepted),
                    "action": .string("focus_session"),
                    "focus": .object(focus.details),
                ])
            )
        }
        guard let action = button.action else {
            throw DeckServiceError.invalidAction(
                "button is not actionable in the current snapshot"
            )
        }

        let updated: DeckSnapshot
        switch action {
        case .refreshSessions:
            updated = try await deckService.refresh(clientID: request.clientID)
        case .previousPage:
            updated = try await deckService.previousPage(
                clientID: request.clientID
            )
        case .nextPage:
            updated = try await deckService.nextPage(
                clientID: request.clientID
            )
        case .chooseNewProvider:
            updated = try await deckService.chooseNewProvider(
                clientID: request.clientID
            )
        case .cancelNewSession:
            updated = try await deckService.cancelNewSession(
                clientID: request.clientID
            )
        case .setSessionIcon:
            guard let optionID = button.optionID,
                let icon = DeckIcon(rawValue: optionID)
            else {
                throw DeckServiceError.invalidAction(
                    "icon option is missing"
                )
            }
            updated = try await deckService.selectSessionIcon(
                clientID: request.clientID,
                icon: icon
            )
        case .setSlotCommand:
            guard let commandID = button.commandID else {
                throw DeckServiceError.invalidAction(
                    "command option is missing"
                )
            }
            updated = try await deckService.selectSlotCommand(
                clientID: request.clientID,
                commandID: commandID
            )
        case .cancelPicker:
            updated = try await deckService.cancelPicker(
                clientID: request.clientID
            )
        case .previousPickerPage:
            updated = try await deckService.previousPickerPage(
                clientID: request.clientID
            )
        case .nextPickerPage:
            updated = try await deckService.nextPickerPage(
                clientID: request.clientID
            )
        case .newSession:
            guard let providerID = button.providerID else {
                throw DeckServiceError.invalidAction(
                    "new session button has no provider target"
                )
            }
            let launch = try await deckService.openNew(
                providerID: providerID
            )
            let completed =
                launch.accepted
                ? try await deckService.completeNewSession(
                    clientID: request.clientID
                )
                : nil
            var response: [String: JSONValue] = [
                "accepted": .boolean(launch.accepted),
                "action": .string(action.rawValue),
                "launch": .object(launch.details),
            ]
            if let completed {
                response["snapshot"] = try jsonValue(completed)
            }
            return try jsonResponse(
                launch.accepted ? .ok : .conflict,
                JSONValue.object(response)
            )
        case .executeCommand:
            guard request.revision == snapshot.revision else {
                throw DeckServiceError.invalidAction(
                    "stale command target must be refreshed before dispatch"
                )
            }
            guard let sessionID = button.sessionID,
                let commandID = button.commandID
            else {
                throw DeckServiceError.invalidAction(
                    "command button has no verified target"
                )
            }
            let command = try await deckService.executeCommand(
                sessionID: sessionID,
                commandID: commandID
            )
            return try jsonResponse(
                command.accepted ? .ok : .conflict,
                JSONValue.object([
                    "accepted": .boolean(command.accepted),
                    "action": .string(action.rawValue),
                    "command": .object(command.details),
                ])
            )
        case .chooseSessionIcon, .chooseSlotCommand:
            throw DeckServiceError.invalidAction(
                "unsupported deck action: \(action.rawValue)"
            )
        }
        return try jsonResponse(
            .ok,
            DeckActivationResponse(
                accepted: true,
                action: action,
                snapshot: updated
            )
        )
    }

    private func longPress(
        _ request: DeckActionRequest
    ) async throws -> HTTPResponse {
        let snapshot = try await deckService.snapshot(
            clientID: request.clientID
        )
        guard
            let button = snapshot.buttons.first(
                where: { $0.id == request.buttonID }
            )
        else {
            throw DeckServiceError.invalidAction(
                "button is absent from the current snapshot"
            )
        }

        let action: DeckAction
        let updated: DeckSnapshot
        if button.kind == .session, let sessionID = button.sessionID {
            action = .chooseSessionIcon
            updated = try await deckService.chooseSessionIcon(
                clientID: request.clientID,
                sessionID: sessionID
            )
        } else if 11..<14 ~= button.position,
            button.action == .executeCommand
        {
            action = .chooseSlotCommand
            updated = try await deckService.chooseSlotCommand(
                clientID: request.clientID,
                index: button.position - 11
            )
        } else {
            throw DeckServiceError.invalidAction(
                "button does not support long press"
            )
        }
        return try jsonResponse(
            .ok,
            DeckActivationResponse(
                accepted: true,
                action: action,
                snapshot: updated
            )
        )
    }

    private func serveAsset(path: String) -> HTTPResponse {
        guard let assetRoot else {
            return jsonError(
                .notFound,
                message: "web assets are unavailable"
            )
        }

        guard let decodedPath = path.removingPercentEncoding,
            isSafeAssetPath(decodedPath)
        else {
            return jsonError(.badRequest, message: "invalid asset path")
        }

        let relativePath =
            decodedPath == "/"
            ? "index.html"
            : String(decodedPath.drop(while: { $0 == "/" }))
        let candidate =
            assetRoot
            .appendingPathComponent(relativePath, isDirectory: false)
            .standardizedFileURL

        if isInsideAssetRoot(candidate),
            let data = try? Data(contentsOf: candidate),
            !candidate.hasDirectoryPath
        {
            return staticResponse(data: data, fileURL: candidate)
        }

        if relativePath.split(separator: "/").last?.contains(".") == true {
            return jsonError(.notFound, message: "asset not found")
        }

        let index = assetRoot.appendingPathComponent("index.html")
        guard let data = try? Data(contentsOf: index) else {
            return jsonError(.notFound, message: "web assets are unavailable")
        }
        return staticResponse(data: data, fileURL: index)
    }

    private func isSafeAssetPath(_ path: String) -> Bool {
        !path.split(separator: "/", omittingEmptySubsequences: true)
            .contains("..")
    }

    private func isInsideAssetRoot(_ candidate: URL) -> Bool {
        guard let assetRoot else { return false }
        let rootPath =
            assetRoot.path.hasSuffix("/")
            ? assetRoot.path
            : assetRoot.path + "/"
        return candidate.path == assetRoot.path
            || candidate.path.hasPrefix(rootPath)
    }

    private func staticResponse(data: Data, fileURL: URL) -> HTTPResponse {
        let isHTML = fileURL.pathExtension.lowercased() == "html"
        var headers = securityHeaders
        headers[.contentType] = contentType(for: fileURL)
        headers[HTTPHeader("Cache-Control")] =
            isHTML
            ? "no-cache"
            : "public, max-age=31536000, immutable"
        return HTTPResponse(
            statusCode: .ok,
            headers: headers,
            body: data
        )
    }

    private func contentType(for fileURL: URL) -> String {
        switch fileURL.pathExtension.lowercased() {
        case "html": "text/html; charset=utf-8"
        case "css": "text/css; charset=utf-8"
        case "js": "text/javascript; charset=utf-8"
        case "json": "application/json"
        case "png": "image/png"
        case "svg": "image/svg+xml"
        case "webp": "image/webp"
        case "ico": "image/x-icon"
        case "woff": "font/woff"
        case "woff2": "font/woff2"
        default: "application/octet-stream"
        }
    }

    private func jsonResponse<Value: Encodable>(
        _ status: HTTPStatusCode,
        _ value: Value
    ) throws -> HTTPResponse {
        var headers = securityHeaders
        headers[.contentType] = "application/json; charset=utf-8"
        headers[HTTPHeader("Cache-Control")] = "no-store"
        return HTTPResponse(
            statusCode: status,
            headers: headers,
            body: try encoder.encode(value)
        )
    }

    private func jsonValue<Value: Encodable>(
        _ value: Value
    ) throws -> JSONValue {
        try decoder.decode(
            JSONValue.self,
            from: encoder.encode(value)
        )
    }

    private func jsonError(
        _ status: HTTPStatusCode,
        message: String
    ) -> HTTPResponse {
        do {
            return try jsonResponse(
                status,
                APIErrorResponse(error: message)
            )
        } catch {
            return HTTPResponse(statusCode: .internalServerError)
        }
    }

    private var securityHeaders: HTTPHeaders {
        [
            HTTPHeader("Content-Security-Policy"):
                "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'",
            HTTPHeader("X-Content-Type-Options"): "nosniff",
            HTTPHeader("Referrer-Policy"): "no-referrer",
        ]
    }

    private static let actionPaths: Set<String> = [
        "/api/activate",
        "/api/long-press",
    ]

    private static func isLoopback(_ address: HTTPRequest.Address?) -> Bool {
        switch address {
        case .ip4("127.0.0.1", port: _), .ip6("::1", port: _):
            true
        case .ip4, .ip6, .unix, .none:
            false
        }
    }

    private enum AuthorizationKind {
        case bearer
        case cookie
    }
}
