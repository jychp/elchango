import AppKit
import ElChangoCore
import ElChangoProviders
import Foundation

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    private enum ServiceState: Equatable {
        case starting
        case running
        case failed(String)

        var label: String {
            switch self {
            case .starting:
                "Starting"
            case .running:
                "Running"
            case .failed:
                "Unavailable"
            }
        }
    }

    private let accessibility = AccessibilityAuthorizer()
    private var service: LoopbackService?
    private var serviceState: ServiceState = .starting
    private var statusItem: NSStatusItem?
    private var refreshTimer: Timer?

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        configureStatusItem()
        let timer = Timer(
            timeInterval: 1,
            repeats: true
        ) { [weak self] _ in
            Task { @MainActor in
                self?.rebuildMenu()
            }
        }
        RunLoop.main.add(timer, forMode: .common)
        refreshTimer = timer
        startService()
    }

    func applicationWillTerminate(_ notification: Notification) {
        refreshTimer?.invalidate()
        if let service {
            Task {
                await service.stop()
            }
        }
    }

    private func configureStatusItem() {
        let item = NSStatusBar.system.statusItem(
            withLength: NSStatusItem.squareLength
        )
        item.button?.image = NSImage(
            systemSymbolName: "rectangle.grid.3x2.fill",
            accessibilityDescription: "elChango"
        )
        item.button?.image?.isTemplate = true
        item.button?.imagePosition = .imageOnly
        statusItem = item
        rebuildMenu()
    }

    private func startService() {
        serviceState = .starting
        rebuildMenu()

        do {
            let preferences = try PreferencesStore()
            let registry = ProviderRegistry()
            let deckService = try DeckService(
                providers: registry.providers,
                preferences: preferences
            )
            let service = try LoopbackService(
                assetRoot: Self.webAssetRoot,
                accessibility: accessibility,
                deckService: deckService,
                unavailableProviders: registry.unavailableProviders
            )
            self.service = service
            Task {
                do {
                    try await service.start()
                    serviceState = .running
                    rebuildMenu()
                    try await service.waitForTermination()
                    serviceState = .failed("loopback service stopped")
                } catch {
                    serviceState = .failed(error.localizedDescription)
                }
                rebuildMenu()
            }
        } catch {
            serviceState = .failed(error.localizedDescription)
            rebuildMenu()
        }
    }

    private func rebuildMenu() {
        let menu = NSMenu()

        let stateItem = NSMenuItem(
            title: "Service: \(serviceState.label)",
            action: nil,
            keyEquivalent: ""
        )
        stateItem.isEnabled = false
        menu.addItem(stateItem)

        let endpointItem = NSMenuItem(
            title: "http://127.0.0.1:\(LoopbackService.defaultPort)",
            action: nil,
            keyEquivalent: ""
        )
        endpointItem.isEnabled = false
        menu.addItem(endpointItem)

        menu.addItem(.separator())
        menu.addItem(
            withTitle: "Open Web Deck",
            action: #selector(openWebDeck),
            keyEquivalent: "o"
        ).target = self

        if !accessibility.isTrusted {
            menu.addItem(
                withTitle: "Request Accessibility Access",
                action: #selector(requestAccessibilityAccess),
                keyEquivalent: ""
            ).target = self
        }

        menu.addItem(
            withTitle: "Diagnostics",
            action: #selector(showDiagnostics),
            keyEquivalent: "d"
        ).target = self
        menu.addItem(.separator())
        menu.addItem(
            withTitle: "Quit elChango",
            action: #selector(quit),
            keyEquivalent: "q"
        ).target = self

        statusItem?.menu = menu
    }

    @objc
    private func openWebDeck() {
        guard let url = URL(
            string: "http://127.0.0.1:\(LoopbackService.defaultPort)/"
        ) else { return }
        NSWorkspace.shared.open(url)
    }

    @objc
    private func requestAccessibilityAccess() {
        _ = accessibility.requestAccess()
        rebuildMenu()
    }

    @objc
    private func showDiagnostics() {
        NSApp.activate(ignoringOtherApps: true)
        let alert = NSAlert()
        alert.messageText = "elChango Diagnostics"
        alert.informativeText = diagnosticsText
        alert.alertStyle = serviceState == .running ? .informational : .warning
        alert.addButton(withTitle: "OK")
        alert.runModal()
    }

    @objc
    private func quit() {
        NSApp.terminate(nil)
    }

    private var diagnosticsText: String {
        let serviceDetails: String
        switch serviceState {
        case .starting:
            serviceDetails = "starting"
        case .running:
            serviceDetails = "running"
        case .failed(let reason):
            serviceDetails = "unavailable: \(reason)"
        }

        return """
        Service: \(serviceDetails)
        Endpoint: http://127.0.0.1:\(LoopbackService.defaultPort)
        Accessibility: \(accessibility.isTrusted ? "granted" : "not granted")
        Web assets: \(Self.webAssetRoot?.path ?? "not found")
        Cursor: native inventory and actions enabled
        Claude Code: native inventory and actions enabled
        """
    }

    private static var webAssetRoot: URL? {
        let fileManager = FileManager.default
        if let configured = ProcessInfo.processInfo.environment[
            "ELCHANGO_WEB_ASSETS"
        ] {
            let url = URL(fileURLWithPath: configured, isDirectory: true)
            if fileManager.fileExists(
                atPath: url.appendingPathComponent("index.html").path
            ) {
                return url
            }
        }

        if let bundled = Bundle.main.resourceURL?
            .appendingPathComponent("Web", isDirectory: true),
           fileManager.fileExists(
               atPath: bundled.appendingPathComponent("index.html").path
           )
        {
            return bundled
        }

        let development = URL(
            fileURLWithPath: fileManager.currentDirectoryPath,
            isDirectory: true
        )
        .appendingPathComponent("../web/dist", isDirectory: true)
        .standardizedFileURL
        if fileManager.fileExists(
            atPath: development.appendingPathComponent("index.html").path
        ) {
            return development
        }
        return nil
    }
}
