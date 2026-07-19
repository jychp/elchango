import AppKit
@preconcurrency import ApplicationServices
import ElChangoCore
import Foundation

public protocol NativeAutomating: Sendable {
    func frontmostBundleID() async -> String?
    func activate(bundleID: String) async throws
    func open(url: URL) async throws
    func postShortcut(
        keyCode: CGKeyCode,
        flags: CGEventFlags,
        bundleID: String
    ) async throws
    func dispatchText(
        _ text: String,
        bundleID: String,
        inputMarker: String,
        focusKeyCode: CGKeyCode?,
        submitCount: Int,
        targetVerifier: @escaping @Sendable () async throws -> Bool
    ) async throws -> ProviderActionResult
    func dispatchCommandEnter(
        bundleID: String,
        inputMarker: String?,
        focusKeyCode: CGKeyCode?,
        targetVerifier: @escaping @Sendable () async throws -> Bool
    ) async throws -> ProviderActionResult
}

public actor NativeAutomation: NativeAutomating {
    public init() {}

    public func frontmostBundleID() async -> String? {
        await MainActor.run {
            NSWorkspace.shared.frontmostApplication?.bundleIdentifier
        }
    }

    nonisolated static func inputIsEmpty(
        characterCount: Int?,
        value: String?,
        placeholderValue: String?
    ) -> Bool {
        if let characterCount {
            return characterCount == 0
        }
        if value?.isEmpty == true || value == "\n" {
            return true
        }
        return placeholderValue?.isEmpty == false
            && value == placeholderValue
    }

    public func activate(bundleID: String) async throws {
        let outcome = await MainActor.run {
            let running = NSRunningApplication.runningApplications(
                withBundleIdentifier: bundleID
            ).filter { !$0.isTerminated }
            if running.count > 1 {
                return -1
            }
            if let application = running.first {
                return application.activate(options: [.activateAllWindows])
                    ? 1
                    : 0
            }
            guard
                let applicationURL = NSWorkspace.shared
                    .urlForApplication(withBundleIdentifier: bundleID)
            else {
                return 0
            }
            return NSWorkspace.shared.open(applicationURL) ? 1 : 0
        }
        guard outcome >= 0 else {
            throw ProviderOperationError.targetUnverified(
                "multiple application instances match \(bundleID)"
            )
        }
        guard outcome == 1 else {
            throw ProviderOperationError.system(
                "cannot activate application \(bundleID)"
            )
        }
        let deadline = ContinuousClock.now.advanced(by: .seconds(3))
        repeat {
            if (try? await requireFrontmost(bundleID: bundleID)) != nil {
                return
            }
            try await sleep(milliseconds: 100)
        } while ContinuousClock.now < deadline
        throw ProviderOperationError.targetUnverified(
            "application did not become frontmost after activation"
        )
    }

    public func open(url: URL) async throws {
        let opened = await MainActor.run {
            NSWorkspace.shared.open(url)
        }
        guard opened else {
            throw ProviderOperationError.system(
                "cannot open \(url.absoluteString)"
            )
        }
    }

    public func postShortcut(
        keyCode: CGKeyCode,
        flags: CGEventFlags,
        bundleID: String
    ) async throws {
        try requireAccessibilityPermission()
        let identity = try await requireFrontmost(bundleID: bundleID)
        try postKey(
            keyCode,
            down: true,
            flags: flags,
            processIdentifier: identity.processIdentifier
        )
        try await sleep(milliseconds: 80)
        _ = try await requireFrontmost(
            bundleID: bundleID,
            processIdentifier: identity.processIdentifier
        )
        try postKey(
            keyCode,
            down: false,
            flags: flags,
            processIdentifier: identity.processIdentifier
        )
    }

    public func dispatchText(
        _ text: String,
        bundleID: String,
        inputMarker: String,
        focusKeyCode: CGKeyCode?,
        submitCount: Int,
        targetVerifier: @escaping @Sendable () async throws -> Bool
    ) async throws -> ProviderActionResult {
        guard !text.isEmpty, submitCount > 0 else {
            throw ProviderOperationError.system(
                "command text and submit count must be valid"
            )
        }
        try requireAccessibilityPermission()
        let started = ContinuousClock.now
        let identity = try await requireFrontmost(bundleID: bundleID)
        prepareAccessibility(processIdentifier: identity.processIdentifier)
        try await sleep(milliseconds: 200)
        if let focusKeyCode {
            try await postShortcut(
                keyCode: focusKeyCode,
                flags: .maskCommand,
                bundleID: bundleID,
                processIdentifier: identity.processIdentifier
            )
            try await sleep(milliseconds: 200)
        }
        let focused = try verifiedFocusedInput(
            processIdentifier: identity.processIdentifier,
            marker: inputMarker,
            requireEmpty: true
        )
        _ = try await requireFrontmost(
            bundleID: bundleID,
            processIdentifier: identity.processIdentifier
        )
        try verifyFocusedInput(
            focused,
            marker: inputMarker,
            requireEmpty: true
        )
        guard try await targetVerifier() else {
            throw ProviderOperationError.targetUnverified(
                "selected provider session changed before text dispatch"
            )
        }
        try await postText(
            text,
            bundleID: bundleID,
            processIdentifier: identity.processIdentifier,
            focusedElement: focused,
            marker: inputMarker
        )
        for _ in 0..<submitCount {
            try await sleep(milliseconds: 500)
            _ = try await requireFrontmost(
                bundleID: bundleID,
                processIdentifier: identity.processIdentifier
            )
            try verifyFocusedInput(
                focused,
                marker: inputMarker,
                requireEmpty: false
            )
            guard try await targetVerifier() else {
                throw ProviderOperationError.targetUnverified(
                    "selected provider session changed before submission"
                )
            }
            try await postFocusedKey(
                keyCode: 36,
                flags: [],
                bundleID: bundleID,
                processIdentifier: identity.processIdentifier,
                focusedElement: focused,
                marker: inputMarker
            )
        }
        _ = try await requireFrontmost(
            bundleID: bundleID,
            processIdentifier: identity.processIdentifier
        )
        return dispatchResult(
            started: started,
            message: "Command text was submitted to the verified provider input."
        )
    }

    public func dispatchCommandEnter(
        bundleID: String,
        inputMarker: String?,
        focusKeyCode: CGKeyCode?,
        targetVerifier: @escaping @Sendable () async throws -> Bool
    ) async throws -> ProviderActionResult {
        try requireAccessibilityPermission()
        let started = ContinuousClock.now
        let identity = try await requireFrontmost(bundleID: bundleID)
        prepareAccessibility(processIdentifier: identity.processIdentifier)
        try await sleep(milliseconds: 200)
        if let focusKeyCode {
            try await postShortcut(
                keyCode: focusKeyCode,
                flags: .maskCommand,
                bundleID: bundleID,
                processIdentifier: identity.processIdentifier
            )
            try await sleep(milliseconds: 200)
        }
        let focused: AXUIElement?
        if let inputMarker {
            focused = try verifiedFocusedInput(
                processIdentifier: identity.processIdentifier,
                marker: inputMarker,
                requireEmpty: false
            )
        } else {
            focused = nil
        }
        _ = try await requireFrontmost(
            bundleID: bundleID,
            processIdentifier: identity.processIdentifier
        )
        guard try await targetVerifier() else {
            throw ProviderOperationError.targetUnverified(
                "selected provider session changed before shortcut dispatch"
            )
        }
        try await postFocusedKey(
            keyCode: 36,
            flags: .maskCommand,
            bundleID: bundleID,
            processIdentifier: identity.processIdentifier,
            focusedElement: focused,
            marker: inputMarker
        )
        _ = try await requireFrontmost(
            bundleID: bundleID,
            processIdentifier: identity.processIdentifier
        )
        return dispatchResult(
            started: started,
            message: "Cmd+Enter was sent to the verified provider."
        )
    }

    private func requireFrontmost(
        bundleID: String,
        processIdentifier: pid_t? = nil
    ) async throws -> ApplicationIdentity {
        let frontmost: ApplicationIdentity? = await MainActor.run {
            () -> ApplicationIdentity? in
            guard let application = NSWorkspace.shared.frontmostApplication,
                let bundleID = application.bundleIdentifier
            else {
                return nil
            }
            return ApplicationIdentity(
                processIdentifier: application.processIdentifier,
                bundleID: bundleID
            )
        }
        guard let identity = frontmost, identity.bundleID == bundleID,
            processIdentifier == nil
                || identity.processIdentifier == processIdentifier
        else {
            throw ProviderOperationError.targetUnverified(
                "command provider is not the frontmost application"
            )
        }
        return identity
    }

    private func prepareAccessibility(processIdentifier: pid_t) {
        let applicationElement = AXUIElementCreateApplication(
            processIdentifier
        )
        AXUIElementSetMessagingTimeout(applicationElement, 1)
        _ = AXUIElementSetAttributeValue(
            applicationElement,
            "AXManualAccessibility" as CFString,
            kCFBooleanTrue
        )
    }

    private func requireAccessibilityPermission() throws {
        guard AXIsProcessTrusted() else {
            throw ProviderOperationError.system(
                "Accessibility permission is required for keyboard dispatch"
            )
        }
    }

    private func verifiedFocusedInput(
        processIdentifier: pid_t,
        marker: String,
        requireEmpty: Bool
    ) throws -> AXUIElement {
        let applicationElement = AXUIElementCreateApplication(
            processIdentifier
        )
        AXUIElementSetMessagingTimeout(applicationElement, 1)
        var value: CFTypeRef?
        let result = AXUIElementCopyAttributeValue(
            applicationElement,
            kAXFocusedUIElementAttribute as CFString,
            &value
        )
        guard result == .success, let value else {
            throw ProviderOperationError.targetUnverified(
                "provider has no focused accessibility element"
            )
        }
        let element = unsafeDowncast(value, to: AXUIElement.self)
        try verifyFocusedInput(
            element,
            marker: marker,
            requireEmpty: requireEmpty
        )
        return element
    }

    private func verifyFocusedInput(
        _ element: AXUIElement,
        marker: String,
        requireEmpty: Bool
    ) throws {
        let role = try stringAttribute(
            element,
            name: kAXRoleAttribute as CFString
        )
        guard
            Set(["AXTextArea", "AXTextField", "AXComboBox"])
                .contains(role)
        else {
            throw ProviderOperationError.targetUnverified(
                "focused element is not a text input: \(role)"
            )
        }
        guard
            try booleanAttribute(
                element,
                name: kAXEnabledAttribute as CFString
            )
        else {
            throw ProviderOperationError.targetUnverified(
                "focused input is disabled"
            )
        }
        let classes = try stringListAttribute(
            element,
            name: "AXDOMClassList" as CFString
        )
        guard classes.joined() == marker else {
            throw ProviderOperationError.targetUnverified(
                "focused input marker does not match"
            )
        }
        if requireEmpty {
            let characterCount = optionalIntegerAttribute(
                element,
                name: "AXNumberOfCharacters" as CFString
            )
            let value = optionalTextAttribute(
                element,
                name: kAXValueAttribute as CFString
            )
            let placeholderValue = optionalTextAttribute(
                element,
                name: kAXPlaceholderValueAttribute as CFString
            )
            guard
                Self.inputIsEmpty(
                    characterCount: characterCount,
                    value: value,
                    placeholderValue: placeholderValue
                )
            else {
                let count = characterCount ?? value?.count ?? -1
                throw ProviderOperationError.targetUnverified(
                    "focused input is not empty (\(count) characters)"
                )
            }
        }
    }

    private func postText(
        _ text: String,
        bundleID: String,
        processIdentifier: pid_t,
        focusedElement: AXUIElement,
        marker: String
    ) async throws {
        let units = Array(text.utf16)
        for start in stride(from: 0, to: units.count, by: 20) {
            _ = try await requireFrontmost(
                bundleID: bundleID,
                processIdentifier: processIdentifier
            )
            try verifyFocusedInput(
                focusedElement,
                marker: marker,
                requireEmpty: start == 0
            )
            let chunk = Array(units[start..<min(start + 20, units.count)])
            guard
                let down = CGEvent(
                    keyboardEventSource: nil,
                    virtualKey: 0,
                    keyDown: true
                ),
                let up = CGEvent(
                    keyboardEventSource: nil,
                    virtualKey: 0,
                    keyDown: false
                )
            else {
                throw ProviderOperationError.system(
                    "macOS failed to create a text keyboard event"
                )
            }
            chunk.withUnsafeBufferPointer { buffer in
                down.keyboardSetUnicodeString(
                    stringLength: buffer.count,
                    unicodeString: buffer.baseAddress
                )
                up.keyboardSetUnicodeString(
                    stringLength: buffer.count,
                    unicodeString: buffer.baseAddress
                )
            }
            down.post(tap: .cghidEventTap)
            up.post(tap: .cghidEventTap)
            try await sleep(milliseconds: 20)
        }
    }

    private func postFocusedKey(
        keyCode: CGKeyCode,
        flags: CGEventFlags,
        bundleID: String,
        processIdentifier: pid_t,
        focusedElement: AXUIElement?,
        marker: String?
    ) async throws {
        _ = try await requireFrontmost(
            bundleID: bundleID,
            processIdentifier: processIdentifier
        )
        if let focusedElement, let marker {
            try verifyFocusedInput(
                focusedElement,
                marker: marker,
                requireEmpty: false
            )
        }
        guard
            let down = CGEvent(
                keyboardEventSource: nil,
                virtualKey: keyCode,
                keyDown: true
            ),
            let up = CGEvent(
                keyboardEventSource: nil,
                virtualKey: keyCode,
                keyDown: false
            )
        else {
            throw ProviderOperationError.system(
                "macOS failed to create a focused keyboard event"
            )
        }
        down.flags = flags
        up.flags = flags
        down.post(tap: .cghidEventTap)
        up.post(tap: .cghidEventTap)
    }

    private func postShortcut(
        keyCode: CGKeyCode,
        flags: CGEventFlags,
        bundleID: String,
        processIdentifier: pid_t
    ) async throws {
        _ = try await requireFrontmost(
            bundleID: bundleID,
            processIdentifier: processIdentifier
        )
        try postKey(
            keyCode,
            down: true,
            flags: flags,
            processIdentifier: processIdentifier
        )
        try await sleep(milliseconds: 80)
        _ = try await requireFrontmost(
            bundleID: bundleID,
            processIdentifier: processIdentifier
        )
        try postKey(
            keyCode,
            down: false,
            flags: flags,
            processIdentifier: processIdentifier
        )
    }

    private func postKey(
        _ keyCode: CGKeyCode,
        down: Bool,
        flags: CGEventFlags,
        processIdentifier: pid_t
    ) throws {
        guard
            let event = CGEvent(
                keyboardEventSource: nil,
                virtualKey: keyCode,
                keyDown: down
            )
        else {
            throw ProviderOperationError.system(
                "macOS failed to create a keyboard event"
            )
        }
        event.flags = flags
        event.postToPid(processIdentifier)
    }

    private func stringAttribute(
        _ element: AXUIElement,
        name: CFString
    ) throws -> String {
        var value: CFTypeRef?
        guard AXUIElementCopyAttributeValue(element, name, &value) == .success,
            let string = value as? String
        else {
            throw ProviderOperationError.targetUnverified(
                "focused element is missing \(name)"
            )
        }
        return string
    }

    private func booleanAttribute(
        _ element: AXUIElement,
        name: CFString
    ) throws -> Bool {
        var value: CFTypeRef?
        guard AXUIElementCopyAttributeValue(element, name, &value) == .success,
            let number = value as? NSNumber
        else {
            throw ProviderOperationError.targetUnverified(
                "focused element is missing \(name)"
            )
        }
        return number.boolValue
    }

    private func optionalIntegerAttribute(
        _ element: AXUIElement,
        name: CFString
    ) -> Int? {
        var value: CFTypeRef?
        guard AXUIElementCopyAttributeValue(element, name, &value) == .success,
            let number = value as? NSNumber
        else {
            return nil
        }
        return number.intValue
    }

    private func optionalTextAttribute(
        _ element: AXUIElement,
        name: CFString
    ) -> String? {
        var value: CFTypeRef?
        guard AXUIElementCopyAttributeValue(element, name, &value) == .success
        else {
            return nil
        }
        if let string = value as? String {
            return string
        }
        if let attributed = value as? NSAttributedString {
            return attributed.string
        }
        return nil
    }

    private func stringListAttribute(
        _ element: AXUIElement,
        name: CFString
    ) throws -> [String] {
        var value: CFTypeRef?
        guard AXUIElementCopyAttributeValue(element, name, &value) == .success
        else {
            throw ProviderOperationError.targetUnverified(
                "focused element is missing \(name)"
            )
        }
        if let strings = value as? [String] {
            return strings
        }
        if let string = value as? String {
            return [string]
        }
        throw ProviderOperationError.targetUnverified(
            "focused element has invalid \(name)"
        )
    }

    private func dispatchResult(
        started: ContinuousClock.Instant,
        message: String
    ) -> ProviderActionResult {
        let duration = started.duration(to: .now)
        let milliseconds =
            duration.components.seconds * 1_000
            + Int64(duration.components.attoseconds / 1_000_000_000_000_000)
        return ProviderActionResult(
            accepted: true,
            verdict: "DISPATCH_VERIFIED",
            details: [
                "executed": .boolean(true),
                "elapsed_ms": .integer(milliseconds),
                "verdict": .string("DISPATCH_VERIFIED"),
                "message": .string(message),
            ]
        )
    }

    private func sleep(milliseconds: UInt64) async throws {
        try await Task.sleep(for: .milliseconds(milliseconds))
    }
}

private struct ApplicationIdentity: Sendable {
    let processIdentifier: pid_t
    let bundleID: String
}
