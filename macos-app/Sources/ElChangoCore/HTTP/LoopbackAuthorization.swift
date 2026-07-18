import Darwin
import Foundation

public enum ControlTokenStoreError: LocalizedError {
    case invalidDirectory(String)
    case invalidTokenFile(String)
    case invalidToken
    case system(String)

    public var errorDescription: String? {
        switch self {
        case .invalidDirectory(let message):
            "Control token directory is unsafe: \(message)"
        case .invalidTokenFile(let message):
            "Control token file is unsafe: \(message)"
        case .invalidToken:
            "Control token has an invalid format"
        case .system(let message):
            "Control token storage failed: \(message)"
        }
    }
}

public struct ControlTokenStore {
    public static var defaultURL: URL {
        FileManager.default.urls(
            for: .applicationSupportDirectory,
            in: .userDomainMask
        )[0]
        .appendingPathComponent("elChango", isDirectory: true)
        .appendingPathComponent("control-token", isDirectory: false)
    }

    public nonisolated let url: URL

    public init(url: URL = Self.defaultURL) {
        self.url = url
    }

    public func loadOrCreate() throws -> String {
        try prepareDirectory()
        if let existing = try readExisting() {
            return existing
        }
        return try create()
    }

    private func prepareDirectory() throws {
        let directory = url.deletingLastPathComponent()
        try FileManager.default.createDirectory(
            at: directory,
            withIntermediateDirectories: true,
            attributes: [.posixPermissions: 0o700]
        )

        var information = stat()
        guard lstat(directory.path, &information) == 0 else {
            throw systemError(path: directory.path)
        }
        guard information.st_mode & S_IFMT == S_IFDIR else {
            throw ControlTokenStoreError.invalidDirectory(
                "path is not a directory"
            )
        }
        guard information.st_uid == getuid() else {
            throw ControlTokenStoreError.invalidDirectory(
                "directory is not owned by the current user"
            )
        }
        guard chmod(directory.path, S_IRWXU) == 0 else {
            throw systemError(path: directory.path)
        }
    }

    private func readExisting() throws -> String? {
        var information = stat()
        guard lstat(url.path, &information) == 0 else {
            if errno == ENOENT {
                return nil
            }
            throw systemError(path: url.path)
        }
        guard information.st_mode & S_IFMT == S_IFREG else {
            throw ControlTokenStoreError.invalidTokenFile(
                "path is not a regular file"
            )
        }
        guard information.st_uid == getuid() else {
            throw ControlTokenStoreError.invalidTokenFile(
                "file is not owned by the current user"
            )
        }
        guard (information.st_mode & 0o777) == (S_IRUSR | S_IWUSR) else {
            throw ControlTokenStoreError.invalidTokenFile(
                "permissions must be exactly 0600"
            )
        }

        let value = try String(
            contentsOf: url,
            encoding: .utf8
        ).trimmingCharacters(in: .whitespacesAndNewlines)
        guard Self.isValid(value) else {
            throw ControlTokenStoreError.invalidToken
        }
        return value
    }

    private func create() throws -> String {
        let value = SecureRandomToken.generate()
        let descriptor = open(
            url.path,
            O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW,
            S_IRUSR | S_IWUSR
        )
        if descriptor == -1 {
            if errno == EEXIST {
                guard let existing = try readExisting() else {
                    throw ControlTokenStoreError.system(
                        "\(url.path): file disappeared during creation"
                    )
                }
                return existing
            }
            throw systemError(path: url.path)
        }

        var succeeded = false
        defer {
            close(descriptor)
            if !succeeded {
                unlink(url.path)
            }
        }

        let data = Data(value.utf8)
        try data.withUnsafeBytes { buffer in
            guard let baseAddress = buffer.baseAddress else { return }
            var offset = 0
            while offset < buffer.count {
                let written = Darwin.write(
                    descriptor,
                    baseAddress.advanced(by: offset),
                    buffer.count - offset
                )
                if written == -1 && errno == EINTR {
                    continue
                }
                guard written > 0 else {
                    throw systemError(path: url.path)
                }
                offset += written
            }
        }
        guard fsync(descriptor) == 0 else {
            throw systemError(path: url.path)
        }
        succeeded = true
        return value
    }

    private static func isValid(_ value: String) -> Bool {
        guard value.count == 43, value.utf8.allSatisfy({
            (65...90).contains($0)
                || (97...122).contains($0)
                || (48...57).contains($0)
                || $0 == 45
                || $0 == 95
        }) else {
            return false
        }
        let base64 = value
            .replacingOccurrences(of: "-", with: "+")
            .replacingOccurrences(of: "_", with: "/")
            + "="
        guard let data = Data(base64Encoded: base64), data.count == 32 else {
            return false
        }
        return SecureRandomToken.encode(data) == value
    }

    private func systemError(path: String) -> ControlTokenStoreError {
        .system("\(path): \(String(cString: strerror(errno)))")
    }
}

public actor LoopbackAuthorization {
    public static let webSessionCookieName = "elchango_session"

    private let controlToken: String
    private let bootstrapLifetime: TimeInterval
    private let sessionLifetime: TimeInterval
    private var bootstraps: [String: Date] = [:]
    private var sessions: [String: Date] = [:]

    public init(
        controlToken: String,
        bootstrapLifetime: TimeInterval = 30,
        sessionLifetime: TimeInterval = 12 * 60 * 60
    ) {
        self.controlToken = controlToken
        self.bootstrapLifetime = bootstrapLifetime
        self.sessionLifetime = sessionLifetime
    }

    public func issueBootstrap(now: Date = Date()) -> String {
        purgeExpired(now: now)
        let token = SecureRandomToken.generate()
        bootstraps[token] = now.addingTimeInterval(bootstrapLifetime)
        return token
    }

    public func consumeBootstrap(
        _ token: String,
        now: Date = Date()
    ) -> String? {
        purgeExpired(now: now)
        guard let expiration = bootstraps.removeValue(forKey: token),
              expiration > now
        else {
            return nil
        }
        let session = SecureRandomToken.generate()
        sessions[session] = now.addingTimeInterval(sessionLifetime)
        return session
    }

    public func acceptsBearer(_ authorization: String?) -> Bool {
        guard let authorization,
              authorization.hasPrefix("Bearer ")
        else {
            return false
        }
        return constantTimeEqual(
            String(authorization.dropFirst("Bearer ".count)),
            controlToken
        )
    }

    public func acceptsCookie(
        _ cookieHeader: String?,
        now: Date = Date()
    ) -> Bool {
        purgeExpired(now: now)
        guard let token = cookieValue(in: cookieHeader),
              let expiration = sessions[token],
              expiration > now
        else {
            return false
        }
        return true
    }

    private func purgeExpired(now: Date) {
        bootstraps = bootstraps.filter { $0.value > now }
        sessions = sessions.filter { $0.value > now }
    }

    private func cookieValue(in header: String?) -> String? {
        header?
            .split(separator: ";")
            .lazy
            .map {
                $0.trimmingCharacters(in: .whitespaces)
                    .split(separator: "=", maxSplits: 1)
            }
            .first {
                $0.count == 2
                    && $0[0] == Substring(Self.webSessionCookieName)
            }
            .map { String($0[1]) }
    }

    private func constantTimeEqual(_ left: String, _ right: String) -> Bool {
        let leftBytes = Array(left.utf8)
        let rightBytes = Array(right.utf8)
        var difference = leftBytes.count ^ rightBytes.count
        for index in 0..<max(leftBytes.count, rightBytes.count) {
            let leftByte = index < leftBytes.count ? leftBytes[index] : 0
            let rightByte = index < rightBytes.count ? rightBytes[index] : 0
            difference |= Int(leftByte ^ rightByte)
        }
        return difference == 0
    }
}

public actor HookRateLimiter {
    private let limit: Int
    private let window: TimeInterval
    private let maximumProviderCount: Int
    private var requests: [String: [Date]] = [:]

    public init(
        limit: Int = 60,
        window: TimeInterval = 60,
        maximumProviderCount: Int = 8
    ) {
        self.limit = limit
        self.window = window
        self.maximumProviderCount = maximumProviderCount
    }

    public func allow(providerID: String, now: Date = Date()) -> Bool {
        let cutoff = now.addingTimeInterval(-window)
        requests = requests.mapValues { $0.filter { $0 > cutoff } }
            .filter { !$0.value.isEmpty }
        let bucket = requests[providerID] != nil
            || requests.count < maximumProviderCount
            ? providerID
            : "_other"
        var recent = requests[bucket, default: []]
        guard recent.count < limit else {
            requests[bucket] = recent
            return false
        }
        recent.append(now)
        requests[bucket] = recent
        return true
    }
}

enum SecureRandomToken {
    static func generate() -> String {
        var generator = SystemRandomNumberGenerator()
        let data = Data(
            (0..<32).map { _ in
                UInt8.random(in: .min ... .max, using: &generator)
            }
        )
        return encode(data)
    }

    static func encode(_ data: Data) -> String {
        return data.base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }
}
