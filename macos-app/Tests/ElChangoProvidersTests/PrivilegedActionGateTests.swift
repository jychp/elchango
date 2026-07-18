import ElChangoProviders
import Testing

@Suite("Process-wide privileged action gate")
struct PrivilegedActionGateTests {
    @Test("a suspended provider action blocks every other provider")
    func serializesAcrossSuspension() async throws {
        let gate = PrivilegedActionGate()
        let recorder = GateRecorder()
        let first = Task {
            try await gate.perform {
                await recorder.append("first-start")
                while !(await recorder.mayFinish()) {
                    try await Task.sleep(for: .milliseconds(1))
                }
                await recorder.append("first-end")
            }
        }
        while await recorder.entries().isEmpty {
            await Task.yield()
        }
        let second = Task {
            try await gate.perform {
                await recorder.append("second")
            }
        }

        try await Task.sleep(for: .milliseconds(20))
        #expect(await recorder.entries() == ["first-start"])

        await recorder.allowFinish()
        try await first.value
        try await second.value
        #expect(
            await recorder.entries()
                == ["first-start", "first-end", "second"]
        )
    }

    @Test("a canceled waiter never dispatches later")
    func cancellationWhileWaiting() async throws {
        let gate = PrivilegedActionGate()
        let recorder = GateRecorder()
        let first = Task {
            try await gate.perform {
                await recorder.append("first")
                while !(await recorder.mayFinish()) {
                    try await Task.sleep(for: .milliseconds(1))
                }
            }
        }
        while await recorder.entries().isEmpty {
            await Task.yield()
        }
        let canceled = Task {
            try await gate.perform {
                await recorder.append("canceled")
            }
        }
        canceled.cancel()
        await recorder.allowFinish()
        try await first.value
        await #expect(throws: CancellationError.self) {
            try await canceled.value
        }
        try await gate.perform {
            await recorder.append("third")
        }

        #expect(await recorder.entries() == ["first", "third"])
    }
}

private actor GateRecorder {
    private var values: [String] = []
    private var finishAllowed = false

    func append(_ value: String) {
        values.append(value)
    }

    func entries() -> [String] {
        values
    }

    func mayFinish() -> Bool {
        finishAllowed
    }

    func allowFinish() {
        finishAllowed = true
    }
}
