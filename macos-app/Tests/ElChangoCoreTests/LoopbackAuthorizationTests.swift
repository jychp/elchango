import Darwin
import ElChangoCore
import Foundation
import Testing

@Suite("Loopback authorization")
struct LoopbackAuthorizationTests {
    @Test("control token is private, stable, and owner-only")
    func tokenStorage() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        let url = root.appendingPathComponent("control-token")
        defer { try? FileManager.default.removeItem(at: root) }

        let store = ControlTokenStore(url: url)
        let created = try store.loadOrCreate()
        let loaded = try store.loadOrCreate()
        let directoryAttributes = try FileManager.default.attributesOfItem(
            atPath: root.path
        )
        let fileAttributes = try FileManager.default.attributesOfItem(
            atPath: url.path
        )

        #expect(created == loaded)
        #expect(created.count == 43)
        #expect(
            directoryAttributes[.posixPermissions] as? NSNumber
                == NSNumber(value: 0o700)
        )
        #expect(
            fileAttributes[.posixPermissions] as? NSNumber
                == NSNumber(value: 0o600)
        )
    }

    @Test("control token rejects symbolic links")
    func tokenRejectsSymlink() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        let target = root.appendingPathComponent("target")
        let token = root.appendingPathComponent("control-token")
        try FileManager.default.createDirectory(
            at: root,
            withIntermediateDirectories: true
        )
        defer { try? FileManager.default.removeItem(at: root) }
        try Data(repeating: 65, count: 43).write(to: target)
        try FileManager.default.createSymbolicLink(
            at: token,
            withDestinationURL: target
        )

        #expect(throws: ControlTokenStoreError.self) {
            _ = try ControlTokenStore(url: token).loadOrCreate()
        }
    }

    @Test("control token rejects group or other access")
    func tokenRejectsBroadPermissions() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        let token = root.appendingPathComponent("control-token")
        try FileManager.default.createDirectory(
            at: root,
            withIntermediateDirectories: true
        )
        defer { try? FileManager.default.removeItem(at: root) }
        try Data(repeating: 65, count: 43).write(to: token)
        #expect(chmod(token.path, 0o644) == 0)

        #expect(throws: ControlTokenStoreError.self) {
            _ = try ControlTokenStore(url: token).loadOrCreate()
        }
    }

    @Test("control token rejects noncanonical base64url")
    func tokenRejectsNoncanonicalEncoding() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        let token = root.appendingPathComponent("control-token")
        try FileManager.default.createDirectory(
            at: root,
            withIntermediateDirectories: true
        )
        defer { try? FileManager.default.removeItem(at: root) }
        let malformed = String(repeating: "A", count: 42) + "B"
        try Data(malformed.utf8).write(to: token)
        #expect(chmod(token.path, 0o600) == 0)

        #expect(throws: ControlTokenStoreError.self) {
            _ = try ControlTokenStore(url: token).loadOrCreate()
        }
    }

    @Test("bootstrap is single-use and creates an expiring web session")
    func bootstrapSession() async throws {
        let authorization = LoopbackAuthorization(
            controlToken: "control",
            bootstrapLifetime: 10,
            sessionLifetime: 20
        )
        let now = Date(timeIntervalSince1970: 100)
        let bootstrap = await authorization.issueBootstrap(now: now)
        let session = try #require(
            await authorization.consumeBootstrap(bootstrap, now: now)
        )

        #expect(
            await authorization.consumeBootstrap(bootstrap, now: now) == nil
        )
        #expect(
            await authorization.acceptsCookie(
                "other=x; elchango_session=\(session)",
                now: now.addingTimeInterval(19)
            )
        )
        #expect(
            !(await authorization.acceptsCookie(
                "elchango_session=\(session)",
                now: now.addingTimeInterval(20)
            ))
        )
    }

    @Test("bearer authentication requires the exact control token")
    func bearer() async {
        let authorization = LoopbackAuthorization(controlToken: "control")

        #expect(await authorization.acceptsBearer("Bearer control"))
        #expect(!(await authorization.acceptsBearer("Bearer wrong")))
        #expect(!(await authorization.acceptsBearer(nil)))
    }

    @Test("hook limiter is isolated per provider and resets")
    func hookLimiter() async {
        let limiter = HookRateLimiter(limit: 2, window: 10)
        let now = Date(timeIntervalSince1970: 100)

        #expect(await limiter.allow(providerID: "cursor", now: now))
        #expect(await limiter.allow(providerID: "cursor", now: now))
        #expect(!(await limiter.allow(providerID: "cursor", now: now)))
        #expect(await limiter.allow(providerID: "claude-code", now: now))
        #expect(
            await limiter.allow(
                providerID: "cursor",
                now: now.addingTimeInterval(11)
            )
        )
    }

    @Test("hook limiter bounds attacker-controlled provider buckets")
    func hookLimiterBoundsProviders() async {
        let limiter = HookRateLimiter(
            limit: 1,
            window: 60,
            maximumProviderCount: 1
        )
        let now = Date(timeIntervalSince1970: 100)

        #expect(await limiter.allow(providerID: "cursor", now: now))
        #expect(await limiter.allow(providerID: "unknown-one", now: now))
        #expect(!(await limiter.allow(providerID: "unknown-two", now: now)))
    }
}
