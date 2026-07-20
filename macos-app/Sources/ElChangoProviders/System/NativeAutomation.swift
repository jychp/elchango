import AppKit
@preconcurrency import ApplicationServices
import ElChangoCore
import Foundation

public enum UnfocusedTextDispatchPolicy: Sendable {
    case reject
    case bestEffort(allowedAccessibilityRoles: Set<String>)
}

public protocol NativeAutomating: Sendable {
    func frontmostBundleID() async -> String?
    func activate(bundleID: String) async throws
    func open(url: URL) async throws
    func postShortcut(
        keyCode: CGKeyCode,
        flags: CGEventFlags,
        bundleID: String
    ) async throws
    func postHeldModifierShortcut(
        modifierKeyCode: CGKeyCode,
        keyCode: CGKeyCode,
        flags: CGEventFlags,
        repeatCount: Int,
        bundleID: String
    ) async throws
    func dispatchText(
        _ text: String,
        bundleID: String,
        inputMarker: String,
        focusKeyCode: CGKeyCode?,
        unfocusedPolicy: UnfocusedTextDispatchPolicy,
        submitCount: Int,
        targetVerifier: @escaping @Sendable () async throws -> Bool
    ) async throws -> ProviderActionResult
    func dispatchCommandEnter(
        bundleID: String,
        inputMarker: String?,
        focusKeyCode: CGKeyCode?,
        targetVerifier: @escaping @Sendable () async throws -> Bool
    ) async throws -> ProviderActionResult
    /// Naive text dispatch: type `text` into whatever has keyboard focus in the
    /// frontmost `bundleID` app, then post the submit key `submitCount` times.
    /// No accessibility-marker verification is performed, so the caller must have
    /// already focused the intended input. Best-effort; for harnesses without a
    /// proven input-target marker.
    func dispatchFrontmostText(
        _ text: String,
        submitKeyCode: CGKeyCode,
        submitFlags: CGEventFlags,
        submitCount: Int,
        bundleID: String
    ) async throws
}

public actor NativeAutomation: NativeAutomating {
    private enum FocusedInputProbe {
        case verified(AXUIElement)
        case nonTextRole(String)
    }

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

    nonisolated static func normalizedInputText(
        value: String,
        characterCount: Int?
    ) -> String? {
        guard characterCount.map({ $0 >= 0 }) ?? true else {
            return nil
        }
        if characterCount == 0 {
            return ""
        }
        return value
    }

    public func activate(bundleID: String) async throws {
        DebugTrace.emit("native-automation", "activate.start \(bundleID)")
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
        DebugTrace.emit("native-automation", "activate.requested \(bundleID)")
        let deadline = ContinuousClock.now.advanced(by: .seconds(3))
        repeat {
            if (try? await requireFrontmost(bundleID: bundleID)) != nil {
                DebugTrace.emit(
                    "native-automation",
                    "activate.frontmost \(bundleID)"
                )
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

    public func dispatchFrontmostText(
        _ text: String,
        submitKeyCode: CGKeyCode,
        submitFlags: CGEventFlags,
        submitCount: Int,
        bundleID: String
    ) async throws {
        guard !text.isEmpty, submitCount > 0 else {
            throw ProviderOperationError.system(
                "command text and submit count must be valid"
            )
        }
        try requireAccessibilityPermission()
        let identity = try await requireFrontmost(bundleID: bundleID)
        try await postBestEffortKeyboardText(
            text,
            bundleID: bundleID,
            processIdentifier: identity.processIdentifier
        )
        for _ in 0..<submitCount {
            try await sleep(milliseconds: 300)
            _ = try await requireFrontmost(
                bundleID: bundleID,
                processIdentifier: identity.processIdentifier
            )
            try postKey(
                submitKeyCode,
                down: true,
                flags: submitFlags,
                processIdentifier: identity.processIdentifier
            )
            try await sleep(milliseconds: 80)
            try postKey(
                submitKeyCode,
                down: false,
                flags: submitFlags,
                processIdentifier: identity.processIdentifier
            )
        }
    }

    public func postHeldModifierShortcut(
        modifierKeyCode: CGKeyCode,
        keyCode: CGKeyCode,
        flags: CGEventFlags,
        repeatCount: Int,
        bundleID: String
    ) async throws {
        guard repeatCount > 0 else {
            throw ProviderOperationError.system(
                "held shortcut repeat count must be positive"
            )
        }
        try requireAccessibilityPermission()
        let identity = try await requireFrontmost(bundleID: bundleID)
        try postGlobalKey(
            modifierKeyCode,
            down: true,
            flags: flags
        )
        defer {
            try? postGlobalKey(
                modifierKeyCode,
                down: false,
                flags: []
            )
        }
        for _ in 0..<repeatCount {
            _ = try await requireFrontmost(
                bundleID: bundleID,
                processIdentifier: identity.processIdentifier
            )
            try postGlobalKey(keyCode, down: true, flags: flags)
            try await sleep(milliseconds: 80)
            try postGlobalKey(keyCode, down: false, flags: flags)
            try await sleep(milliseconds: 120)
        }
    }

    public func dispatchText(
        _ text: String,
        bundleID: String,
        inputMarker: String,
        focusKeyCode: CGKeyCode?,
        unfocusedPolicy: UnfocusedTextDispatchPolicy,
        submitCount: Int,
        targetVerifier: @escaping @Sendable () async throws -> Bool
    ) async throws -> ProviderActionResult {
        DebugTrace.emit("native-automation", "dispatch_text.start \(bundleID)")
        guard !text.isEmpty, submitCount > 0 else {
            throw ProviderOperationError.system(
                "command text and submit count must be valid"
            )
        }
        try requireAccessibilityPermission()
        let started = ContinuousClock.now
        try await activate(bundleID: bundleID)
        let identity = try await requireFrontmost(bundleID: bundleID)
        DebugTrace.emit("native-automation", "dispatch_text.activated")
        guard try await targetVerifier() else {
            throw ProviderOperationError.targetUnverified(
                "selected provider session changed after application activation"
            )
        }
        prepareAccessibility(processIdentifier: identity.processIdentifier)
        try await sleep(milliseconds: 200)
        var focused: AXUIElement?
        var nonTextRole: String?
        if focusKeyCode == nil {
            switch try probeFocusedInput(
                processIdentifier: identity.processIdentifier,
                marker: inputMarker
            ) {
            case .verified(let element):
                focused = element
            case .nonTextRole(let role):
                nonTextRole = role
            }
        } else {
            focused = try? verifiedFocusedInput(
                processIdentifier: identity.processIdentifier,
                marker: inputMarker
            )
        }
        if focused == nil, let focusKeyCode {
            try await postShortcut(
                keyCode: focusKeyCode,
                flags: .maskCommand,
                bundleID: bundleID,
                processIdentifier: identity.processIdentifier
            )
            try await sleep(milliseconds: 200)
        }
        if focused == nil, focusKeyCode == nil {
            guard
                let nonTextRole,
                Self.bestEffortAllowed(
                    role: nonTextRole,
                    policy: unfocusedPolicy
                )
            else {
                throw ProviderOperationError.targetUnverified(
                    "provider has no eligible unfocused input route"
                )
            }
            return try await dispatchBestEffortText(
                text,
                submitCount: submitCount,
                started: started,
                bundleID: bundleID,
                processIdentifier: identity.processIdentifier,
                targetVerifier: targetVerifier
            )
        }
        if focused == nil {
            focused = try verifiedFocusedInput(
                processIdentifier: identity.processIdentifier,
                marker: inputMarker
            )
        }
        guard var focused else {
            throw ProviderOperationError.targetUnverified(
                "provider has no verified focused input"
            )
        }
        DebugTrace.emit("native-automation", "dispatch_text.input_verified")
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
        guard
            let draft = Self.boundedDraft(
                try textAttribute(
                    focused,
                    name: kAXSelectedTextAttribute as CFString
                )
            )
        else {
            throw ProviderOperationError.targetUnverified(
                "selected provider draft exceeds the safe in-memory limit"
            )
        }
        DebugTrace.emit(
            "native-automation",
            "dispatch_text.draft_captured units=\(draft.utf16.count)"
        )
        guard try await targetVerifier() else {
            throw ProviderOperationError.targetUnverified(
                "selected provider session changed before text replacement"
            )
        }
        var submitAttempted = false
        do {
            DebugTrace.emit("native-automation", "dispatch_text.replace.start")
            try await postKeyboardText(
                text,
                bundleID: bundleID,
                processIdentifier: identity.processIdentifier,
                focusedElement: focused,
                marker: inputMarker
            )
            try await sleep(milliseconds: 100)
            DebugTrace.emit("native-automation", "dispatch_text.replace.complete")
            focused = try verifiedFocusedInput(
                processIdentifier: identity.processIdentifier,
                marker: inputMarker
            )
            try verifyInputText(focused, expected: text)
            DebugTrace.emit("native-automation", "dispatch_text.command_verified")
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
                submitAttempted = true
                DebugTrace.emit("native-automation", "dispatch_text.submit_posted")
            }
            DebugTrace.emit("native-automation", "dispatch_text.wait_empty.start")
            focused = try await waitForEmptyFocusedInput(
                bundleID: bundleID,
                processIdentifier: identity.processIdentifier,
                marker: inputMarker,
                targetVerifier: targetVerifier
            )
            DebugTrace.emit("native-automation", "dispatch_text.wait_empty.complete")
            if !draft.isEmpty {
                DebugTrace.emit("native-automation", "dispatch_text.restore.start")
                try await postKeyboardText(
                    draft,
                    bundleID: bundleID,
                    processIdentifier: identity.processIdentifier,
                    focusedElement: focused,
                    marker: inputMarker
                )
                try await sleep(milliseconds: 100)
                try verifyInputText(focused, expected: draft)
                DebugTrace.emit("native-automation", "dispatch_text.restore.complete")
            }
            guard try await targetVerifier() else {
                throw ProviderOperationError.targetUnverified(
                    "selected provider session changed during draft restoration"
                )
            }
        } catch {
            DebugTrace.failure(
                "native-automation",
                "dispatch_text.failed",
                error: error
            )
            if (try? await targetVerifier()) == true {
                let current = try? verifiedFocusedInput(
                    processIdentifier: identity.processIdentifier,
                    marker: inputMarker
                )
                let currentText: String?
                if let current {
                    currentText = try? inputText(current)
                } else {
                    currentText = nil
                }
                if !submitAttempted
                    || currentText.map({
                        Self.inputTextMatches($0, expected: text)
                    }) == true
                {
                    try? await replaceFocusedText(
                        with: draft,
                        bundleID: bundleID,
                        processIdentifier: identity.processIdentifier,
                        marker: inputMarker
                    )
                }
            }
            throw error
        }
        _ = try await requireFrontmost(
            bundleID: bundleID,
            processIdentifier: identity.processIdentifier
        )
        DebugTrace.emit("native-automation", "dispatch_text.complete")
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
        try await activate(bundleID: bundleID)
        let identity = try await requireFrontmost(bundleID: bundleID)
        guard try await targetVerifier() else {
            throw ProviderOperationError.targetUnverified(
                "selected provider session changed after application activation"
            )
        }
        prepareAccessibility(processIdentifier: identity.processIdentifier)
        try await sleep(milliseconds: 200)
        var focused: AXUIElement?
        if let inputMarker {
            focused = try? verifiedFocusedInput(
                processIdentifier: identity.processIdentifier,
                marker: inputMarker
            )
        }
        if focused == nil, let focusKeyCode {
            try await postShortcut(
                keyCode: focusKeyCode,
                flags: .maskCommand,
                bundleID: bundleID,
                processIdentifier: identity.processIdentifier
            )
            try await sleep(milliseconds: 200)
        }
        if focused == nil, let inputMarker {
            focused = try verifiedFocusedInput(
                processIdentifier: identity.processIdentifier,
                marker: inputMarker
            )
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

    nonisolated static func bestEffortAllowed(
        role: String,
        policy: UnfocusedTextDispatchPolicy
    ) -> Bool {
        switch policy {
        case .reject:
            return false
        case .bestEffort(let allowedAccessibilityRoles):
            return allowedAccessibilityRoles.contains(role)
        }
    }

    private func probeFocusedInput(
        processIdentifier: pid_t,
        marker: String
    ) throws -> FocusedInputProbe {
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
        let role = try stringAttribute(
            element,
            name: kAXRoleAttribute as CFString
        )
        guard
            Set(["AXTextArea", "AXTextField", "AXComboBox"]).contains(role)
        else {
            return .nonTextRole(role)
        }
        try verifyFocusedInput(element, marker: marker)
        return .verified(element)
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
        try verifyFocusedInput(focusedElement, marker: marker)
        try await postGlobalShortcut(
            keyCode: 0,
            flags: .maskCommand,
            bundleID: bundleID,
            processIdentifier: processIdentifier
        )
        try await sleep(milliseconds: 100)
        return try verifiedFocusedInput(
            processIdentifier: processIdentifier,
            marker: marker
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
        if !text.isEmpty {
            try await postKeyboardText(
                text,
                bundleID: bundleID,
                processIdentifier: processIdentifier,
                focusedElement: selected,
                marker: marker
            )
            try await sleep(milliseconds: 100)
        } else {
            try await postGlobalShortcut(
                keyCode: 51,
                flags: [],
                bundleID: bundleID,
                processIdentifier: processIdentifier
            )
            try await sleep(milliseconds: 100)
        }
        try verifyInputText(selected, expected: text)
    }

    private func waitForEmptyFocusedInput(
        bundleID: String,
        processIdentifier: pid_t,
        marker: String,
        targetVerifier: @escaping @Sendable () async throws -> Bool
    ) async throws -> AXUIElement {
        let deadline = ContinuousClock.now.advanced(by: .seconds(3))
        repeat {
            _ = try await requireFrontmost(
                bundleID: bundleID,
                processIdentifier: processIdentifier
            )
            guard try await targetVerifier() else {
                throw ProviderOperationError.targetUnverified(
                    "selected provider session changed after submission"
                )
            }
            if let focused = try? verifiedFocusedInput(
                processIdentifier: processIdentifier,
                marker: marker
            ),
                let selected = try? await selectAll(
                    bundleID: bundleID,
                    processIdentifier: processIdentifier,
                    marker: marker,
                    focusedElement: focused
                ),
                let value = try? textAttribute(
                    selected,
                    name: kAXSelectedTextAttribute as CFString
                ),
                Self.inputTextMatches(value, expected: "")
            {
                return selected
            }
            try await sleep(milliseconds: 100)
        } while ContinuousClock.now < deadline
        throw ProviderOperationError.targetUnverified(
            "provider input did not clear after command submission"
        )
    }

    private func dispatchBestEffortText(
        _ text: String,
        submitCount: Int,
        started: ContinuousClock.Instant,
        bundleID: String,
        processIdentifier: pid_t,
        targetVerifier: @escaping @Sendable () async throws -> Bool
    ) async throws -> ProviderActionResult {
        DebugTrace.emit(
            "native-automation",
            "dispatch_text.best_effort.start"
        )
        guard try await targetVerifier() else {
            throw ProviderOperationError.targetUnverified(
                "selected provider session changed before best-effort dispatch"
            )
        }
        try await postBestEffortKeyboardText(
            text,
            bundleID: bundleID,
            processIdentifier: processIdentifier
        )
        for _ in 0..<submitCount {
            try await sleep(milliseconds: 500)
            _ = try await requireFrontmost(
                bundleID: bundleID,
                processIdentifier: processIdentifier
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
                processIdentifier: processIdentifier,
                focusedElement: nil,
                marker: nil
            )
        }
        _ = try await requireFrontmost(
            bundleID: bundleID,
            processIdentifier: processIdentifier
        )
        guard try await targetVerifier() else {
            throw ProviderOperationError.targetUnverified(
                "selected provider session changed during best-effort dispatch"
            )
        }
        DebugTrace.emit(
            "native-automation",
            "dispatch_text.best_effort.complete"
        )
        return dispatchResult(
            started: started,
            message: "Command text was sent through provider best-effort routing."
        )
    }

    private func postBestEffortKeyboardText(
        _ text: String,
        bundleID: String,
        processIdentifier: pid_t
    ) async throws {
        let units = Array(text.utf16)
        for start in stride(from: 0, to: units.count, by: 20) {
            _ = try await requireFrontmost(
                bundleID: bundleID,
                processIdentifier: processIdentifier
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
            down.flags = []
            up.flags = []
            down.post(tap: .cghidEventTap)
            up.post(tap: .cghidEventTap)
            try await sleep(milliseconds: 20)
        }
    }

    private func postKeyboardText(
        _ text: String,
        bundleID: String,
        processIdentifier: pid_t,
        focusedElement: AXUIElement,
        marker: String
    ) async throws {
        let units = Array(text.utf16)
        DebugTrace.emit(
            "native-automation",
            "keyboard_text.start units=\(units.count) chunks=\((units.count + 19) / 20)"
        )
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
            down.flags = []
            up.flags = []
            down.post(tap: .cghidEventTap)
            up.post(tap: .cghidEventTap)
            try await sleep(milliseconds: 20)
        }
        DebugTrace.emit("native-automation", "keyboard_text.complete")
    }

    private func verifyInputText(
        _ element: AXUIElement,
        expected: String
    ) throws {
        let value = try inputText(element)
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
        DebugTrace.emit(
            "native-automation",
            "focused_key.start key=\(keyCode)"
        )
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
        DebugTrace.emit(
            "native-automation",
            "focused_key.complete key=\(keyCode)"
        )
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

    private func postGlobalShortcut(
        keyCode: CGKeyCode,
        flags: CGEventFlags,
        bundleID: String,
        processIdentifier: pid_t
    ) async throws {
        _ = try await requireFrontmost(
            bundleID: bundleID,
            processIdentifier: processIdentifier
        )
        if flags.contains(.maskCommand) {
            try await postGlobalCommandShortcut(
                keyCode: keyCode,
                flags: flags,
                bundleID: bundleID,
                processIdentifier: processIdentifier
            )
            return
        }
        try postGlobalKey(keyCode, down: true, flags: flags)
        do {
            try await sleep(milliseconds: 80)
            _ = try await requireFrontmost(
                bundleID: bundleID,
                processIdentifier: processIdentifier
            )
            try postGlobalKey(keyCode, down: false, flags: flags)
        } catch {
            try? postGlobalKey(keyCode, down: false, flags: flags)
            throw error
        }
    }

    private func postGlobalCommandShortcut(
        keyCode: CGKeyCode,
        flags: CGEventFlags,
        bundleID: String,
        processIdentifier: pid_t
    ) async throws {
        try postGlobalKey(55, down: true, flags: .maskCommand)
        var shortcutKeyIsDown = false
        defer {
            if shortcutKeyIsDown {
                try? postGlobalKey(keyCode, down: false, flags: flags)
            }
            try? postGlobalKey(55, down: false, flags: [])
        }
        try postGlobalKey(keyCode, down: true, flags: flags)
        shortcutKeyIsDown = true
        try await sleep(milliseconds: 80)
        _ = try await requireFrontmost(
            bundleID: bundleID,
            processIdentifier: processIdentifier
        )
        try postGlobalKey(keyCode, down: false, flags: flags)
        shortcutKeyIsDown = false
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

    private func postGlobalKey(
        _ keyCode: CGKeyCode,
        down: Bool,
        flags: CGEventFlags
    ) throws {
        guard
            let event = CGEvent(
                keyboardEventSource: nil,
                virtualKey: keyCode,
                keyDown: down
            )
        else {
            throw ProviderOperationError.system(
                "macOS failed to create a global keyboard event"
            )
        }
        event.flags = flags
        event.post(tap: .cghidEventTap)
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

    private func inputText(_ element: AXUIElement) throws -> String {
        let value = try textAttribute(
            element,
            name: kAXValueAttribute as CFString
        )
        let characterCount = optionalIntegerAttribute(
            element,
            name: "AXNumberOfCharacters" as CFString
        )
        guard
            let normalized = Self.normalizedInputText(
                value: value,
                characterCount: characterCount
            )
        else {
            throw ProviderOperationError.targetUnverified(
                "focused input value contradicts its character count"
            )
        }
        return normalized
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
