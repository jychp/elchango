import ElChangoCore
import Foundation
import Testing

@Suite("Native provider activity")
struct ActivityStoreTests {
    @Test("Claude payload retains only bounded background metadata")
    func claudePayloadSanitization() throws {
        let data = Data(
            """
            {
              "hook_event_name": "Stop",
              "session_id": "claude-1",
              "prompt_id": "prompt-1",
              "cwd": "/tmp/project",
              "transcript_path": "/tmp/claude-1.jsonl",
              "source": "compact",
              "background_tasks": [{
                "id": "task-1",
                "type": "subagent",
                "status": "running",
                "agent_type": "Explore",
                "description": "discard this",
                "command": "discard this"
              }],
              "last_assistant_message": "discard this"
            }
            """.utf8
        )

        let payload = try JSONDecoder().decode(
            ProviderHookPayload.self,
            from: data
        )
        let task = try #require(payload.backgroundTasks?.first)
        #expect(payload.promptID == "prompt-1")
        #expect(payload.source == "compact")
        #expect(task.taskID == "task-1")
        #expect(task.taskType == "subagent")
        #expect(task.status == "running")
        #expect(task.agentType == "Explore")
        #expect(
            !String(
                decoding: try JSONEncoder().encode(payload),
                as: UTF8.self
            ).contains("discard this")
        )
    }

    @Test("Cursor progress and completion follow one generation")
    func cursorProgressAndCompletion() throws {
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
        let progress = try store.record(
            ProviderHookPayload(
                hookEventName: "afterAgentThought",
                conversationID: "cursor-1",
                generationID: "generation-1"
            ),
            observedAtMilliseconds: 150
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
        _ = try store.record(
            ProviderHookPayload(
                hookEventName: "stop",
                conversationID: "cursor-1",
                status: "completed"
            ),
            observedAtMilliseconds: 250
        )

        #expect(progress.state == .working)
        #expect(progress.detail.contains("planned"))
        let state = try #require(
            store.state(
                for: "cursor-1",
                observedAtMilliseconds: 200
            )
        )

        #expect(state.0 == .done)
        #expect(state.1 == .observed)
    }

    @Test("Cursor completion is acknowledged only by explicit focus")
    func cursorFocusAcknowledgement() throws {
        let store = CursorActivityStore(ttlMilliseconds: 1_000)
        _ = try store.record(
            ProviderHookPayload(
                hookEventName: "stop",
                conversationID: "cursor-1",
                status: "completed"
            ),
            observedAtMilliseconds: 100
        )
        store.acknowledge(
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

    @Test("Cursor ignores terminal evidence from an older generation")
    func cursorGenerationOrdering() throws {
        let store = CursorActivityStore(ttlMilliseconds: 1_000)
        _ = try store.record(
            ProviderHookPayload(
                hookEventName: "beforeSubmitPrompt",
                conversationID: "cursor-1",
                generationID: "new"
            ),
            observedAtMilliseconds: 100
        )
        _ = try store.record(
            ProviderHookPayload(
                hookEventName: "stop",
                conversationID: "cursor-1",
                generationID: "old",
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
        #expect(state.0 == .working)
    }

    @Test("Cursor defers parent completion until parallel subagents stop")
    func cursorParallelSubagents() throws {
        let store = CursorActivityStore(ttlMilliseconds: 1_000)
        for subagentID in ["child-1", "child-2"] {
            _ = try store.record(
                ProviderHookPayload(
                    hookEventName: "subagentStart",
                    conversationID: "cursor-1",
                    generationID: "generation-1",
                    subagentID: subagentID
                ),
                observedAtMilliseconds: 100
            )
        }
        _ = try store.record(
            ProviderHookPayload(
                hookEventName: "subagentStart",
                conversationID: "cursor-1",
                generationID: "generation-1",
                subagentID: "child-1"
            ),
            observedAtMilliseconds: 125
        )
        _ = try store.record(
            ProviderHookPayload(
                hookEventName: "subagentStop",
                conversationID: "cursor-1",
                generationID: "generation-1",
                subagentID: "unknown"
            ),
            observedAtMilliseconds: 150
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
        _ = try store.record(
            ProviderHookPayload(
                hookEventName: "subagentStop",
                conversationID: "cursor-1",
                generationID: "generation-1",
                status: "completed",
                subagentID: "child-1"
            ),
            observedAtMilliseconds: 300
        )
        #expect(
            store.state(
                for: "cursor-1",
                observedAtMilliseconds: 300
            )?.0 == .working
        )
        _ = try store.record(
            ProviderHookPayload(
                hookEventName: "subagentStop",
                conversationID: "cursor-1",
                generationID: "generation-1",
                status: "aborted",
                subagentID: "child-2"
            ),
            observedAtMilliseconds: 400
        )
        #expect(
            store.state(
                for: "cursor-1",
                observedAtMilliseconds: 400
            )?.0 == .error
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

    @Test("Claude prompt IDs reject stale terminal events")
    func claudePromptOrdering() throws {
        let store = ClaudeActivityStore()
        _ = try store.record(
            ProviderHookPayload(
                hookEventName: "UserPromptSubmit",
                sessionID: "claude-1",
                cwd: "/tmp/project",
                transcriptPath: "/tmp/claude-1.jsonl",
                promptID: "new"
            ),
            observedAtMilliseconds: 100
        )
        _ = try store.record(
            ProviderHookPayload(
                hookEventName: "Stop",
                sessionID: "claude-1",
                cwd: "/tmp/project",
                transcriptPath: "/tmp/claude-1.jsonl",
                promptID: "old",
                backgroundTasks: []
            ),
            observedAtMilliseconds: 200
        )
        #expect(
            store.state(
                for: "claude-1",
                observedAtMilliseconds: 200
            )?.0 == .working
        )
    }

    @Test("Claude waits for background tasks and a later empty Stop")
    func claudeBackgroundTasks() throws {
        let store = ClaudeActivityStore()
        _ = try store.record(
            ProviderHookPayload(
                hookEventName: "Stop",
                sessionID: "claude-1",
                cwd: "/tmp/project",
                transcriptPath: "/tmp/claude-1.jsonl",
                promptID: "prompt-1",
                backgroundTasks: [
                    ProviderHookBackgroundTask(
                        taskID: "task-1",
                        taskType: "subagent",
                        status: "running"
                    )
                ]
            ),
            observedAtMilliseconds: 100
        )
        #expect(
            store.state(
                for: "claude-1",
                observedAtMilliseconds: 100
            )?.0 == .working
        )
        _ = try store.record(
            ProviderHookPayload(
                hookEventName: "PostToolBatch",
                sessionID: "claude-1",
                cwd: "/tmp/project",
                transcriptPath: "/tmp/claude-1.jsonl",
                promptID: "prompt-1"
            ),
            observedAtMilliseconds: 200
        )
        _ = try store.record(
            ProviderHookPayload(
                hookEventName: "Stop",
                sessionID: "claude-1",
                cwd: "/tmp/project",
                transcriptPath: "/tmp/claude-1.jsonl",
                promptID: "prompt-1",
                backgroundTasks: []
            ),
            observedAtMilliseconds: 300
        )
        #expect(
            store.state(
                for: "claude-1",
                observedAtMilliseconds: 300
            )?.0 == .done
        )
    }

    @Test("Claude current notifications and compaction map conservatively")
    func claudeNotificationAndCompaction() throws {
        let store = ClaudeActivityStore()
        let idle = try store.record(
            ProviderHookPayload(
                hookEventName: "Notification",
                sessionID: "claude-1",
                cwd: "/tmp/project",
                transcriptPath: "/tmp/claude-1.jsonl",
                notificationType: "idle_prompt"
            ),
            observedAtMilliseconds: 100
        )
        #expect(idle.state == .done)

        let compact = try store.record(
            ProviderHookPayload(
                hookEventName: "PreCompact",
                sessionID: "claude-2",
                cwd: "/tmp/project",
                transcriptPath: "/tmp/claude-2.jsonl",
                compactTrigger: "auto"
            ),
            observedAtMilliseconds: 100
        )
        #expect(compact.state == .working)

        let resumed = try store.record(
            ProviderHookPayload(
                hookEventName: "Notification",
                sessionID: "claude-3",
                cwd: "/tmp/project",
                transcriptPath: "/tmp/claude-3.jsonl",
                notificationType: "elicitation_response"
            ),
            observedAtMilliseconds: 100
        )
        #expect(resumed.state == .working)

        let agentCompleted = try store.record(
            ProviderHookPayload(
                hookEventName: "Notification",
                sessionID: "claude-4",
                cwd: "/tmp/project",
                transcriptPath: "/tmp/claude-4.jsonl",
                notificationType: "agent_completed"
            ),
            observedAtMilliseconds: 100
        )
        #expect(agentCompleted.state == .working)
        #expect(agentCompleted.confidence == .candidate)

        let compactResume = try store.record(
            ProviderHookPayload(
                hookEventName: "SessionStart",
                sessionID: "claude-5",
                cwd: "/tmp/project",
                transcriptPath: "/tmp/claude-5.jsonl",
                source: "compact"
            ),
            observedAtMilliseconds: 100
        )
        #expect(compactResume.state == .working)
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
