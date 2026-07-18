import Foundation

public enum FoundationDeck {
    public static func snapshot(
        observedAt: Date = Date()
    ) -> DeckSnapshot {
        var buttons = (0..<10).map { position in
            DeckButton(
                id: "empty:\(position)",
                position: position,
                kind: .empty,
                label: "Available",
                detail: "No session",
                icon: .plus,
                enabled: false,
                action: .chooseNewProvider
            )
        }

        buttons.append(
            DeckButton(
                id: "control:refresh",
                position: 10,
                kind: .control,
                label: "Refresh",
                detail: "Reorder by activity",
                icon: .arrowsClockwise,
                enabled: true,
                action: .refreshSessions
            )
        )
        buttons.append(
            DeckButton(
                id: "command:11:accept",
                position: 11,
                kind: .control,
                label: "Accept",
                detail: "",
                icon: .check,
                enabled: false,
                action: .executeCommand,
                commandID: .accept
            )
        )
        buttons.append(
            DeckButton(
                id: "command:12:commit_push",
                position: 12,
                kind: .control,
                label: "Commit Push",
                detail: "",
                icon: .gitCommit,
                enabled: false,
                action: .executeCommand,
                commandID: .commitPush
            )
        )
        buttons.append(
            DeckButton(
                id: "command:13:create_pr",
                position: 13,
                kind: .control,
                label: "Open PR",
                detail: "",
                icon: .gitPullRequest,
                enabled: false,
                action: .executeCommand,
                commandID: .createPR
            )
        )
        buttons.append(
            DeckButton(
                id: "control:new",
                position: 14,
                kind: .control,
                label: "New",
                detail: "Create agent",
                icon: .plus,
                enabled: false,
                action: .chooseNewProvider
            )
        )

        return DeckSnapshot(
            revision: 1,
            observedAtMilliseconds: Int64(observedAt.timeIntervalSince1970 * 1_000),
            source: "no providers available",
            readOnly: true,
            selectedSessionID: nil,
            page: 1,
            pageCount: 1,
            hasPrevious: false,
            hasNext: false,
            buttons: buttons
        )
    }
}
