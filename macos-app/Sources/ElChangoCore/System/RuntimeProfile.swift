import Foundation

public enum RuntimeProfileError: LocalizedError, Equatable {
    case unsupportedBundleIdentifier(String)

    public var errorDescription: String? {
        switch self {
        case .unsupportedBundleIdentifier(let identifier):
            "Unsupported elChango bundle identifier: \(identifier)"
        }
    }
}

public struct RuntimeProfile: Equatable, Sendable {
    public enum Name: String, Sendable {
        case stable
        case debug
    }

    public static let stableBundleIdentifier = "com.jychp.elchango"
    public static let debugBundleIdentifier = "com.jychp.elchango.debug"

    public let name: Name
    public let displayName: String
    public let bundleIdentifier: String
    public let applicationSupportDirectoryName: String

    public static func resolve(
        bundleIdentifier: String
    ) throws -> RuntimeProfile {
        switch bundleIdentifier {
        case stableBundleIdentifier:
            .stable
        case debugBundleIdentifier:
            .debug
        default:
            throw RuntimeProfileError.unsupportedBundleIdentifier(
                bundleIdentifier
            )
        }
    }

    public static let stable = RuntimeProfile(
        name: .stable,
        displayName: "elChango",
        bundleIdentifier: stableBundleIdentifier,
        applicationSupportDirectoryName: "elChango"
    )

    public static let debug = RuntimeProfile(
        name: .debug,
        displayName: "elChango-debug",
        bundleIdentifier: debugBundleIdentifier,
        applicationSupportDirectoryName: "elChango-debug"
    )

    public var preferencesURL: URL {
        FileManager.default.urls(
            for: .applicationSupportDirectory,
            in: .userDomainMask
        )[0]
        .appendingPathComponent(
            applicationSupportDirectoryName,
            isDirectory: true
        )
        .appendingPathComponent("preferences.json", isDirectory: false)
    }

    public var controlTokenURL: URL {
        ControlTokenStore.defaultURL
    }

    public var serviceLeaseURL: URL {
        ServiceLease.defaultURL
    }
}
