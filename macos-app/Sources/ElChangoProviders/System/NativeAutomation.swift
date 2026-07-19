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
    private static let maximumDraftBytes = 64 * 1_024

    public init() {}

    public func frontmostBundleID() async -> String? {
        await MainActor.run {
            NSWorkspace.shared.frontmostApplication?.bundleIdentifier
        }
    }

    nonisolated static func boundedDraft(
        _ value: String?,
        maximumBytes: Int = maximumDraftBytes
    ) -> String? {
        guard let value, value.utf8.count <= maximumBytes else {
            return nil
        }
        return value
    }

    nonisolated static func inputTextMatches(
        _ value: String,
        expected: String
    ) -> Bool {
        value == expected || value == "\(expected)\n"
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
        var focused = try verifiedFocusedInput(
            processIdentifier: identity.processIdentifier,
            marker: inputMarker
        )
        _ = try await requireFrontmost(
            bundleID: bundleID,
            processIdentifier: identity.processIdentifier
        )
        try verifyFocusedInput(
            focused,
            marker: inputMarker
        )
        guard try await targetVerifier() else {
            throw ProviderOperationError.targetUnverified(
                "selected provider session changed before text dispatch"
            )
        }
        focused = try await selectAll(
            bundleID: bundleID,
            processIdentifier: identity.processIdentifier,
            marker: inputMarker,
            focusedElement: focused
        )
        guard try await targetVerifier() else {
            throw ProviderOperationError.targetUnverified(
                "selected provider session changed before draft capture"
            )
        }
        guard
            let draft = Self.boundedDraft(
                try selectedTextAttribute(focused)
            )
        else {
            throw ProviderOperationError.targetUnverified(
                "selected provider draft exceeds the safe in-memory limit"
            )
        }
        try await postText(
            text,
            bundleID: bundleID,
            processIdentifier: identity.processIdentifier,
            focusedElement: focused,
            marker: inputMarker
        )
        try verifyInputText(focused, expected: text)
        var submitted = false
        do {
            for _ in 0..<submitCount {
                try await sleep(milliseconds: 500)
                _ = try await requireFrontmost(
                    bundleID: bundleID,
                    processIdentifier: identity.processIdentifier
                )
                try verifyFocusedInput(
                    focused,
                    marker: inputMarker
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
                submitted = true
            }
        } catch {
            if !submitted, try await targetVerifier() {
                try? await replaceFocusedText(
                    with: draft,
                    bundleID: bundleID,
                    processIdentifier: identity.processIdentifier,
                    marker: inputMarker
                )
            }
            throw error
        }
        if !draft.isEmpty {
            try await sleep(milliseconds: 300)
            guard try await targetVerifier() else {
                throw ProviderOperationError.targetUnverified(
                    "selected provider session changed before draft restoration"
                )
            }
            try await restoreDraft(
                draft,
                bundleID: bundleID,
                processIdentifier: identity.processIdentifier,
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
                marker: inputMarker
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
        marker: String
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
            marker: marker
        )
        return element
    }

    private func verifyFocusedInput(
        _ element: AXUIElement,
        marker: String
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
    }

    private func selectAll(
        bundleID: String,
        processIdentifier: pid_t,
        marker: String,
        focusedElement: AXUIElement
    ) async throws -> AXUIElement {
        try await postFocusedKey(
            keyCode: 0,
            flags: .maskCommand,
            bundleID: bundleID,
            processIdentifier: processIdentifier,
            focusedElement: focusedElement,
            marker: marker
        )
        try await sleep(milliseconds: 100)
        return try verifiedFocusedInput(
            processIdentifier: processIdentifier,
            marker: marker
        )
    }

    private func selectedTextAttribute(
        _ element: AXUIElement
    ) throws -> String {
        var value: CFTypeRef?
        let result = AXUIElementCopyAttributeValue(
            element,
            kAXSelectedTextAttribute as CFString,
            &value
        )
        if result == .noValue {
            return ""
        }
        guard result == .success else {
            throw ProviderOperationError.targetUnverified(
                "focused input does not expose selected text"
            )
        }
        if let string = value as? String {
            return string
        }
        if let attributed = value as? NSAttributedString {
            return attributed.string
        }
        throw ProviderOperationError.targetUnverified(
            "focused input selected text has an unsupported type"
        )
    }

    private func replaceFocusedText(
        with text: String,
        bundleID: String,
        processIdentifier: pid_t,
        marker: String
    ) async throws {
        let focused = try verifiedFocusedInput(
            processIdentifier: processIdentifier,
            marker: marker
        )
        let selected = try await selectAll(
            bundleID: bundleID,
            processIdentifier: processIdentifier,
            marker: marker,
            focusedElement: focused
        )
        if text.isEmpty {
            try await postFocusedKey(
                keyCode: 51,
                flags: [],
                bundleID: bundleID,
                processIdentifier: processIdentifier,
                focusedElement: selected,
                marker: marker
            )
        } else {
            try await postText(
                text,
                bundleID: bundleID,
                processIdentifier: processIdentifier,
                focusedElement: selected,
                marker: marker
            )
            try verifyInputText(selected, expected: text)
        }
    }

    private func restoreDraft(
        _ draft: String,
        bundleID: String,
        processIdentifier: pid_t,
        marker: String
    ) async throws {
        let focused = try verifiedFocusedInput(
            processIdentifier: processIdentifier,
            marker: marker
        )
        let selected = try await selectAll(
            bundleID: bundleID,
            processIdentifier: processIdentifier,
            marker: marker,
            focusedElement: focused
        )
        guard try selectedTextAttribute(selected).isEmpty else {
            throw ProviderOperationError.targetUnverified(
                "provider input changed before draft restoration"
            )
        }
        try await postText(
            draft,
            bundleID: bundleID,
            processIdentifier: processIdentifier,
            focusedElement: selected,
            marker: marker
        )
        try verifyInputText(selected, expected: draft)
    }

    private func postText(
        _ text: String,
        bundleID: String,
        processIdentifier: pid_t,
        focusedElement: AXUIElement,
        marker: String
    ) async throws {
        let setResult = AXUIElementSetAttributeValue(
            focusedElement,
            kAXSelectedTextAttribute as CFString,
            text as CFString
        )
        if setResult == .success {
            return
        }
        guard
            setResult == .attributeUnsupported
                || setResult == .illegalArgument
                || setResult == .notImplemented
        else {
            throw ProviderOperationError.system(
                "macOS failed to replace the selected provider text"
            )
        }
        let units = Array(text.utf16)
        for start in stride(from: 0, to: units.count, by: 20) {
            _ = try await requireFrontmost(
                bundleID: bundleID,
                processIdentifier: processIdentifier
            )
            try verifyFocusedInput(
                focusedElement,
                marker: marker
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

    private func verifyInputText(
        _ element: AXUIElement,
        expected: String
    ) throws {
        let value = try textAttribute(
            element,
            name: kAXValueAttribute as CFString
        )
        guard Self.inputTextMatches(value, expected: expected) else {
            throw ProviderOperationError.targetUnverified(
                "provider input did not accept the command text"
            )
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
                marker: marker
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

    private func textAttribute(
        _ element: AXUIElement,
        name: CFString
    ) throws -> String {
        var value: CFTypeRef?
        guard AXUIElementCopyAttributeValue(element, name, &value) == .success
        else {
            throw ProviderOperationError.targetUnverified(
                "focused element is missing \(name)"
            )
        }
        if let string = value as? String {
            return string
        }
        if let attributed = value as? NSAttributedString {
            return attributed.string
        }
        throw ProviderOperationError.targetUnverified(
            "focused element has unsupported text for \(name)"
        )
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
