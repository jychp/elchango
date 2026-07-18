import Foundation

enum DeckLayout {
    static let sessionSlots = 10
    static let totalButtons = 15

    static func sessionButtons(
        sessions: [AgentSession?],
        page: Int,
        hasNext: Bool,
        launchEnabled: Bool,
        preferences: DeckPreferences,
        commandTarget: AgentSession?
    ) -> [DeckButton] {
        var buttons = sessions.enumerated().map { position, session in
            guard let session else {
                return DeckButton(
                    id: "empty:\(position)",
                    position: position,
                    kind: .empty,
                    label: "Available",
                    detail: "No session",
                    icon: .plus,
                    enabled: launchEnabled,
                    action: .chooseNewProvider
                )
            }
            return DeckButton(
                id: "session:\(session.id)",
                position: position,
                kind: .session,
                label: session.title,
                detail: "",
                icon: preferences.sessionIcons[session.id] ?? session.icon,
                color: displayColor(for: session.state),
                selected: session.selected,
                enabled: session.capabilities.contains(.focusSession),
                confidence: session.confidence,
                sessionID: session.id,
                providerID: session.providerID
            )
        }

        buttons.append(
            page == 1
                ? DeckButton(
                    id: "control:refresh",
                    position: 10,
                    kind: .control,
                    label: "Refresh",
                    detail: "Reorder by activity",
                    icon: .arrowsClockwise,
                    enabled: true,
                    action: .refreshSessions
                )
                : DeckButton(
                    id: "control:previous",
                    position: 10,
                    kind: .control,
                    label: "Previous",
                    detail: "Page \(page - 1)",
                    icon: .arrowLeft,
                    enabled: true,
                    action: .previousPage
                )
        )

        for (offset, commandID) in preferences.actionSlots.enumerated() {
            let position = offset + 11
            let presentation = commandPresentation(commandID)
            buttons.append(
                DeckButton(
                    id: "command:\(position):\(commandID.rawValue)",
                    position: position,
                    kind: .control,
                    label: presentation.label,
                    detail: "",
                    icon: presentation.icon,
                    enabled: commandTarget?.commands.contains(commandID) == true,
                    sessionID: commandTarget?.id,
                    action: .executeCommand,
                    providerID: commandTarget?.providerID,
                    commandID: commandID
                )
            )
        }

        buttons.append(
            hasNext
                ? DeckButton(
                    id: "control:next",
                    position: 14,
                    kind: .control,
                    label: "Next",
                    detail: "Page \(page + 1)",
                    icon: .arrowRight,
                    enabled: true,
                    action: .nextPage
                )
                : DeckButton(
                    id: "control:new",
                    position: 14,
                    kind: .control,
                    label: "New",
                    detail: "Create agent",
                    icon: .plus,
                    enabled: launchEnabled,
                    action: .chooseNewProvider
                )
        )
        return buttons
    }

    static func providerPicker(
        providers: [ProviderDescriptor]
    ) throws -> [DeckButton] {
        let positions = try providerPositions(providers.count)
        var buttons = blankButtons()
        for (position, provider) in zip(positions, providers) {
            buttons[position] = DeckButton(
                id: "provider:\(provider.id)",
                position: position,
                kind: .control,
                label: provider.displayName,
                detail: "Create agent",
                icon: provider.icon,
                enabled: true,
                action: .newSession,
                providerID: provider.id
            )
        }
        buttons[10] = DeckButton(
            id: "control:cancel-new",
            position: 10,
            kind: .control,
            label: "Cancel",
            detail: "Return to sessions",
            icon: .arrowLeft,
            enabled: true,
            action: .cancelNewSession
        )
        return buttons
    }

    static func iconPicker(
        pageIndex: Int
    ) throws -> (buttons: [DeckButton], pageCount: Int) {
        let pageCount = max(
            1,
            (
                DeckIcon.personalizationOptions.count
                    + sessionSlots - 1
            ) / sessionSlots
        )
        guard 0..<pageCount ~= pageIndex else {
            throw DeckServiceError.invalidAction(
                "session icon picker page is out of range"
            )
        }

        let start = pageIndex * sessionSlots
        let options = DeckIcon.personalizationOptions[
            start..<min(
                start + sessionSlots,
                DeckIcon.personalizationOptions.count
            )
        ]
        var buttons = blankButtons()
        for (position, icon) in options.enumerated() {
            buttons[position] = DeckButton(
                id: "icon:\(icon.rawValue)",
                position: position,
                kind: .control,
                label: title(for: icon.rawValue),
                detail: "Session icon",
                icon: icon,
                enabled: true,
                action: .setSessionIcon,
                optionID: icon.rawValue
            )
        }
        buttons[10] = DeckButton(
            id: pageIndex == 0 ? "picker:cancel" : "picker:previous",
            position: 10,
            kind: .control,
            label: pageIndex == 0 ? "Cancel" : "Previous",
            detail: "",
            icon: .arrowLeft,
            enabled: true,
            action: pageIndex == 0 ? .cancelPicker : .previousPickerPage
        )
        buttons[14] = DeckButton(
            id: pageIndex + 1 < pageCount ? "picker:next" : "picker:cancel",
            position: 14,
            kind: .control,
            label: pageIndex + 1 < pageCount ? "Next" : "Cancel",
            detail: "",
            icon: pageIndex + 1 < pageCount ? .arrowRight : .arrowLeft,
            enabled: true,
            action: pageIndex + 1 < pageCount
                ? .nextPickerPage
                : .cancelPicker
        )
        return (buttons, pageCount)
    }

    static func commandPicker() throws -> [DeckButton] {
        let commands: [CommandID] = [
            .accept,
            .createPR,
            .commitPush,
            .compact,
        ]
        let positions = try providerPositions(commands.count)
        var buttons = blankButtons()
        for (position, commandID) in zip(positions, commands) {
            let presentation = commandPresentation(commandID)
            buttons[position] = DeckButton(
                id: "command-option:\(commandID.rawValue)",
                position: position,
                kind: .control,
                label: presentation.label,
                detail: "Assign action",
                icon: presentation.icon,
                enabled: true,
                action: .setSlotCommand,
                commandID: commandID,
                optionID: commandID.rawValue
            )
        }
        buttons[10] = DeckButton(
            id: "picker:cancel",
            position: 10,
            kind: .control,
            label: "Cancel",
            detail: "",
            icon: .arrowLeft,
            enabled: true,
            action: .cancelPicker
        )
        return buttons
    }

    static func providerPositions(_ count: Int) throws -> [Int] {
        switch count {
        case 1: [7]
        case 2: [6, 8]
        case 3: [6, 7, 8]
        case 4: [5, 6, 8, 9]
        case 5: [5, 6, 7, 8, 9]
        default:
            throw DeckServiceError.invalidAction(
                "provider chooser supports between one and five providers"
            )
        }
    }

    static func commandPresentation(
        _ commandID: CommandID
    ) -> (label: String, icon: DeckIcon) {
        switch commandID {
        case .accept:
            ("Accept", .check)
        case .createPR:
            ("Open PR", .gitPullRequest)
        case .commitPush:
            ("Commit Push", .gitCommit)
        case .compact:
            ("Compact", .article)
        }
    }

    private static func blankButtons() -> [DeckButton] {
        (0..<totalButtons).map { position in
            DeckButton(
                id: "empty:\(position)",
                position: position,
                kind: .empty,
                label: "",
                detail: "",
                icon: .arrowsClockwise,
                color: .unknown,
                enabled: false,
                confidence: .unknown
            )
        }
    }

    private static func title(for rawValue: String) -> String {
        rawValue
            .replacingOccurrences(of: "-", with: " ")
            .split(separator: " ")
            .map { word in
                word.prefix(1).uppercased() + word.dropFirst()
            }
            .joined(separator: " ")
    }

    private static func displayColor(
        for state: SessionState
    ) -> DeckColor {
        switch state {
        case .error:
            .waiting
        case .unknown:
            .idle
        case .idle:
            .idle
        case .working:
            .working
        case .waiting:
            .waiting
        case .done:
            .done
        }
    }
}
