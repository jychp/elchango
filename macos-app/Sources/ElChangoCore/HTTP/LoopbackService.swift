import Darwin
import FlyingFox
import FlyingSocks
import Foundation

public actor LoopbackService {
    public static let defaultPort: UInt16 = 8_765

    private let server: HTTPServer
    private var runTask: Task<Void, any Error>?

    public init(
        port: UInt16 = LoopbackService.defaultPort,
        assetRoot: URL?,
        accessibility: any AccessibilityChecking,
        deckService: DeckService,
        unavailableProviders: [String: String] = [:]
    ) throws {
        let address = try sockaddr_in.inet(ip4: "127.0.0.1", port: port)
        let configuration = HTTPServer.Configuration(
            address: address,
            timeout: 2,
            sharedRequestBufferSize: FoundationHTTPHandler.actionBodyLimit,
            sharedRequestReplaySize: FoundationHTTPHandler.hookBodyLimit
        )
        self.server = HTTPServer(
            config: configuration,
            handler: FoundationHTTPHandler(
                assetRoot: assetRoot,
                accessibility: accessibility,
                deckService: deckService,
                unavailableProviders: unavailableProviders
            )
        )
    }

    public func start() async throws {
        guard runTask == nil else { return }
        runTask = Task {
            try await server.run()
        }
        do {
            try await server.waitUntilListening(timeout: 2)
        } catch {
            runTask?.cancel()
            runTask = nil
            await server.stop()
            throw error
        }
    }

    public var isRunning: Bool {
        get async {
            await server.isListening
        }
    }

    public func waitForTermination() async throws {
        guard let runTask else { return }
        try await runTask.value
    }

    public func stop() async {
        runTask?.cancel()
        await server.stop()
        runTask = nil
    }
}
