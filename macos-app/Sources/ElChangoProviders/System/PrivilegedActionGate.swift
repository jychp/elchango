import Foundation

public actor PrivilegedActionGate {
    private var isLocked = false
    private var waiters: [(id: UUID, continuation: CheckedContinuation<Bool, Never>)] =
        []
    private var activeIdentifier: UUID?

    public init() {}

    public func perform<Value: Sendable>(
        _ operation: @escaping @Sendable () async throws -> Value
    ) async throws -> Value {
        let identifier = UUID()
        let acquired = await withTaskCancellationHandler {
            await acquire(identifier)
        } onCancel: {
            Task {
                await self.cancel(identifier)
            }
        }
        guard acquired else {
            throw CancellationError()
        }
        if Task.isCancelled {
            release()
            throw CancellationError()
        }
        do {
            let task = Task.detached(operation: operation)
            let value = try await task.value
            release()
            return value
        } catch {
            release()
            throw error
        }
    }

    private func acquire(_ identifier: UUID) async -> Bool {
        if Task.isCancelled {
            return false
        }
        guard isLocked else {
            isLocked = true
            activeIdentifier = identifier
            return true
        }
        return await withCheckedContinuation { continuation in
            waiters.append((identifier, continuation))
        }
    }

    private func release() {
        guard !waiters.isEmpty else {
            isLocked = false
            activeIdentifier = nil
            return
        }
        let waiter = waiters.removeFirst()
        activeIdentifier = waiter.id
        waiter.continuation.resume(returning: true)
    }

    private func cancel(_ identifier: UUID) {
        guard activeIdentifier != identifier else {
            return
        }
        if let index = waiters.firstIndex(where: { $0.id == identifier }) {
            let waiter = waiters.remove(at: index)
            waiter.continuation.resume(returning: false)
        }
    }
}
