import Foundation
@preconcurrency import ServiceManagement

/// A provider-agnostic view of `SMAppService.Status` so callers and tests need
/// not import ServiceManagement.
public enum LoginItemStatus: Sendable {
    /// Registered and allowed to launch at login.
    case enabled
    /// Not registered as a login item.
    case disabled
    /// Registered but awaiting the user's approval in System Settings.
    case requiresApproval
    /// The service could not be found, e.g. the app is not a registered bundle.
    case notFound
}

/// Reads and toggles whether elChango launches at login. Mirrors
/// `AccessibilityAuthorizer`: a small, injectable seam so the app delegate and
/// its tests never touch `SMAppService` directly.
public protocol LoginItemManaging: Sendable {
    /// The current login-item registration state.
    var status: LoginItemStatus { get }
    /// Register the main app to launch at login. Throws if the call fails.
    func enable() throws
    /// Remove the main app from login items. Throws if the call fails.
    func disable() throws
    /// Open System Settings to the Login Items pane so the user can approve a
    /// registration that is pending, or review the current state.
    func openSettings()
}

public struct LoginItemAuthorizer: LoginItemManaging, Sendable {
    public init() {}

    public var status: LoginItemStatus {
        switch SMAppService.mainApp.status {
        case .enabled:
            return .enabled
        case .notRegistered:
            return .disabled
        case .requiresApproval:
            return .requiresApproval
        case .notFound:
            return .notFound
        @unknown default:
            return .notFound
        }
    }

    public func enable() throws {
        try SMAppService.mainApp.register()
    }

    public func disable() throws {
        try SMAppService.mainApp.unregister()
    }

    public func openSettings() {
        SMAppService.openSystemSettingsLoginItems()
    }
}
