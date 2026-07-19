import ElChangoCore
import Foundation
import Testing

@Suite("Runtime profiles")
struct RuntimeProfileTests {
    @Test("stable and debug identities stay distinct")
    func distinctIdentities() throws {
        let stable = try RuntimeProfile.resolve(
            bundleIdentifier: RuntimeProfile.stableBundleIdentifier
        )
        let debug = try RuntimeProfile.resolve(
            bundleIdentifier: RuntimeProfile.debugBundleIdentifier
        )

        #expect(stable.name == .stable)
        #expect(debug.name == .debug)
        #expect(stable.bundleIdentifier != debug.bundleIdentifier)
        #expect(
            stable.applicationSupportDirectoryName
                != debug.applicationSupportDirectoryName
        )
        #expect(stable.preferencesURL != debug.preferencesURL)
        #expect(stable.controlTokenURL == debug.controlTokenURL)
        #expect(stable.serviceLeaseURL == debug.serviceLeaseURL)
    }

    @Test("unknown app identities fail closed")
    func unknownIdentity() {
        #expect(throws: RuntimeProfileError.self) {
            try RuntimeProfile.resolve(
                bundleIdentifier: "com.example.untrusted"
            )
        }
    }

    @Test("shared service lease rejects a concurrent owner")
    func exclusiveServiceLease() throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        let lockURL = directory.appendingPathComponent("service.lock")
        defer {
            try? FileManager.default.removeItem(at: directory)
        }

        let first = try ServiceLease(holder: "stable", url: lockURL)
        _ = withExtendedLifetime(first) {
            #expect(throws: ServiceLeaseError.self) {
                try ServiceLease(holder: "debug", url: lockURL)
            }
        }
    }
}
