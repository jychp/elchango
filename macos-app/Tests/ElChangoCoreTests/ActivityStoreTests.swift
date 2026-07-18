import ElChangoCore
import Testing

@Suite("Native provider activity")
struct ActivityStoreTests {
    @Test("Cursor plan completion waits for approval")
    func cursorPlanCompletion() throws {
        let store = CursorActivityStore(ttlMilliseconds: 1_000)
        _ = try store.record(
            ProviderHookPayload(
                hookEventName: "beforeSubmitPrompt",
                conversationID: "cursor-1",
                generationID: "generation-1",
                composerMode: "plan"
            ),
            observedAtMilliseconds: 100
        )
        _ = try store.record(
            ProviderHookPayload(
                hookEventName: "stop",
                conversationID: "cursor-1",
                generationID: "generation-1",
                status: "completed"
            ),
            observedAtMilliseconds: 200
        )

        let state = try #require(
            store.state(
                for: "cursor-1",
                observedAtMilliseconds: 200
            )
        )

        #expect(state.0 == .waiting)
        #expect(state.1 == .observed)
    }

    @Test("Cursor completion is acknowledged only after selection changes")
    func cursorSelectionAcknowledgement() throws {
        let store = CursorActivityStore(ttlMilliseconds: 1_000)
        store.observeSelection(
            "cursor-2",
            observedAtMilliseconds: 50
        )
        _ = try store.record(
            ProviderHookPayload(
                hookEventName: "stop",
                conversationID: "cursor-1",
                status: "completed"
            ),
            observedAtMilliseconds: 100
        )
        store.observeSelection(
            "cursor-1",
            observedAtMilliseconds: 200
        )

        let state = try #require(
            store.state(
                for: "cursor-1",
                observedAtMilliseconds: 200
            )
        )

        #expect(state.0 == .idle)
        #expect(state.2 == "completion acknowledged by focus")
    }

    @Test("Cursor terminal evidence cannot cross generations")
    func cursorGenerationMismatch() throws {
        let store = CursorActivityStore(ttlMilliseconds: 1_000)
        _ = try store.record(
            ProviderHookPayload(
                hookEventName: "stop",
                conversationID: "cursor-1",
                generationID: "old",
                status: "completed"
            ),
            observedAtMilliseconds: 100
        )

        #expect(
            store.state(
                for: "cursor-1",
                observedAtMilliseconds: 100,
                currentGenerationID: "new"
            ) == nil
        )
    }

    @Test("activity stores evict the oldest bounded signal")
    func boundedEviction() throws {
        let store = CursorActivityStore(
            ttlMilliseconds: 1_000,
            maximumSignals: 1
        )
        _ = try store.record(
            ProviderHookPayload(
                hookEventName: "sessionStart",
                conversationID: "old"
            ),
            observedAtMilliseconds: 100
        )
        _ = try store.record(
            ProviderHookPayload(
                hookEventName: "sessionStart",
                conversationID: "new"
            ),
            observedAtMilliseconds: 200
        )

        #expect(
            store.state(
                for: "old",
                observedAtMilliseconds: 200
            ) == nil
        )
    }

    @Test("Claude documented hooks map conservatively")
    func claudeStateMapping() throws {
        let store = ClaudeActivityStore(
            terminalDeadlineMilliseconds: 100,
            ttlMilliseconds: 1_000
        )
        let base = ProviderHookPayload(
            hookEventName: "PermissionRequest",
            sessionID: "claude-1",
            cwd: "/tmp/project",
            transcriptPath: "/tmp/claude-1.jsonl",
            toolName: "Bash"
        )

        let observation = try store.record(
            base,
            observedAtMilliseconds: 100
        )

        #expect(observation.state == .waiting)
        #expect(observation.detail.contains("Bash"))
    }

    @Test("Claude missing terminal events degrade explicitly")
    func claudeTerminalDeadline() throws {
        let store = ClaudeActivityStore(
            terminalDeadlineMilliseconds: 100,
            ttlMilliseconds: 1_000
        )
        _ = try store.record(
            ProviderHookPayload(
                hookEventName: "UserPromptSubmit",
                sessionID: "claude-1",
                cwd: "/tmp/project",
                transcriptPath: "/tmp/claude-1.jsonl"
            ),
            observedAtMilliseconds: 100
        )

        let state = try #require(
            store.state(
                for: "claude-1",
                observedAtMilliseconds: 201
            )
        )

        #expect(state.0 == .unknown)
        #expect(state.1 == .unknown)
        #expect(state.2.contains("terminal event was not observed"))
    }

    @Test("Claude rejects unrelated tool events")
    func claudeUnsupportedWaitingTool() {
        let store = ClaudeActivityStore()

        #expect(throws: ProviderOperationError.self) {
            try store.record(
                ProviderHookPayload(
                    hookEventName: "PreToolUse",
                    sessionID: "claude-1",
                    cwd: "/tmp/project",
                    transcriptPath: "/tmp/claude-1.jsonl",
                    toolName: "Read"
                ),
                observedAtMilliseconds: 100
            )
        }
    }
}
