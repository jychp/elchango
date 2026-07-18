import FlyingFox
import Foundation

public struct FoundationHTTPHandler: HTTPHandler {
    public static let actionBodyLimit = 4_096
    public static let hookBodyLimit = 65_536

    private let assetRoot: URL?
    private let accessibility: any AccessibilityChecking
    private let encoder: JSONEncoder

    public init(
        assetRoot: URL?,
        accessibility: any AccessibilityChecking
    ) {
        self.assetRoot = assetRoot?.standardizedFileURL
        self.accessibility = accessibility
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        self.encoder = encoder
    }

    public func handleRequest(_ request: HTTPRequest) async throws -> HTTPResponse {
        if request.method == .GET {
            switch request.path {
            case "/api/health":
                return try jsonResponse(
                    .ok,
                    HealthResponse(
                        focusEnabled: false,
                        launchEnabled: false,
                        actionsEnabled: false,
                        providers: [:],
                        unavailableProviders: [
                            "cursor": "provider not migrated to native host",
                            "claude-code": "provider not migrated to native host",
                        ],
                        accessibilityTrusted: accessibility.isTrusted
                    )
                )
            case "/api/snapshot":
                let clientID = request.query["client_id"] ?? "web"
                guard Self.isValidClientID(clientID) else {
                    return try jsonResponse(
                        .badRequest,
                        APIErrorResponse(error: "invalid client_id")
                    )
                }
                return try jsonResponse(.ok, FoundationDeck.snapshot())
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
            return try await rejectUnavailableAction(request)
        }

        return try jsonResponse(
            .methodNotAllowed,
            APIErrorResponse(error: "method not allowed")
        )
    }

    private func rejectUnavailableAction(
        _ request: HTTPRequest
    ) async throws -> HTTPResponse {
        let limit: Int
        let isHook = request.path.hasPrefix("/api/hooks/")
        if isHook {
            limit = Self.hookBodyLimit
        } else if Self.actionPaths.contains(request.path) {
            limit = Self.actionBodyLimit
        } else {
            return try jsonResponse(
                .methodNotAllowed,
                APIErrorResponse(
                    error: "actions are not enabled in the native foundation"
                )
            )
        }

        guard request.headers[.contentType]?.lowercased()
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
        guard body.count <= limit else {
            return try jsonResponse(
                .payloadTooLarge,
                APIErrorResponse(error: "request body is too large")
            )
        }

        if isHook {
            return try jsonResponse(
                .notFound,
                APIErrorResponse(error: "unknown hook provider")
            )
        }

        return try jsonResponse(
            .serviceUnavailable,
            APIErrorResponse(
                error: "native provider actions are not available yet",
                retryable: false
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

        let relativePath = decodedPath == "/"
            ? "index.html"
            : String(decodedPath.drop(while: { $0 == "/" }))
        let candidate = assetRoot
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
        let rootPath = assetRoot.path.hasSuffix("/")
            ? assetRoot.path
            : assetRoot.path + "/"
        return candidate.path == assetRoot.path
            || candidate.path.hasPrefix(rootPath)
    }

    private func staticResponse(data: Data, fileURL: URL) -> HTTPResponse {
        let isHTML = fileURL.pathExtension.lowercased() == "html"
        var headers = securityHeaders
        headers[.contentType] = contentType(for: fileURL)
        headers[HTTPHeader("Cache-Control")] = isHTML
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
        "/api/focus",
        "/api/intent",
    ]

    private static func isValidClientID(_ value: String) -> Bool {
        guard 1...128 ~= value.utf8.count else { return false }
        return value.unicodeScalars.allSatisfy { scalar in
            scalar.isASCII && (
                CharacterSet.alphanumerics.contains(scalar)
                    || "._:-".unicodeScalars.contains(scalar)
            )
        }
    }
}
