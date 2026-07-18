@preconcurrency import ApplicationServices
import Foundation

public protocol AccessibilityChecking: Sendable {
    var isTrusted: Bool { get }
}

public struct AccessibilityAuthorizer: AccessibilityChecking, Sendable {
    public init() {}

    public var isTrusted: Bool {
        AXIsProcessTrusted()
    }

    @discardableResult
    public func requestAccess() -> Bool {
        let promptKey = kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String
        return AXIsProcessTrustedWithOptions([promptKey: true] as CFDictionary)
    }
}
