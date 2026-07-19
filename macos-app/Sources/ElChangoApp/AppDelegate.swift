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
    private var runtimeProfile: RuntimeProfile = .stable
    private var serviceLease: ServiceLease?
    private var service: LoopbackService?
    private var serviceState: ServiceState = .starting
    private var statusItem: NSStatusItem?
    private var refreshTimer: Timer?
    private var providerStatuses = [
        "cursor": "starting",
        "claude-code": "starting",
    ]

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
            systemSymbolName: "square.grid.3x3.fill",
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
            runtimeProfile = try RuntimeProfile.resolve(
                bundleIdentifier: Bundle.main.bundleIdentifier ?? ""
            )
            serviceLease = try ServiceLease(
                holder: runtimeProfile.displayName,
                url: runtimeProfile.serviceLeaseURL
            )
            let controlToken = try ControlTokenStore(
                url: runtimeProfile.controlTokenURL
            )
            .loadOrCreate()
            let preferences = try PreferencesStore(
                url: runtimeProfile.preferencesURL
            )
            let registry = ProviderRegistry()
            let enabledProviderIDs = Set(
                registry.providers.map { $0.descriptor.id }
            )
            providerStatuses = Dictionary(
                uniqueKeysWithValues: ["cursor", "claude-code"].map { id in
                    if let reason = registry.unavailableProviders[id] {
                        return (id, "unavailable: \(reason)")
                    }
                    return (
                        id,
                        enabledProviderIDs.contains(id)
                            ? "enabled"
                            : "disabled"
                    )
                }
            )
            let deckService = try DeckService(
                providers: registry.providers,
                preferences: preferences
            )
            let service = try LoopbackService(
                assetRoot: Self.webAssetRoot,
                accessibility: accessibility,
                deckService: deckService,
                controlToken: controlToken,
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
            if case ServiceLeaseError.alreadyRunning = error {
                showExclusiveLaunchFailure(error.localizedDescription)
            }
        }
    }

    private func rebuildMenu() {
        let menu = NSMenu()

        let version =
            Bundle.main.object(
                forInfoDictionaryKey: "CFBundleShortVersionString"
            ) as? String ?? "unknown"
        let titleItem = NSMenuItem(
            title: "",
            action: nil,
            keyEquivalent: ""
        )
        titleItem.view = menuHeaderView(version: version)
        menu.addItem(titleItem)

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
        let webDeckItem = menu.addItem(
            withTitle: "Open Web Deck",
            action: #selector(openWebDeck),
            keyEquivalent: "o"
        )
        webDeckItem.target = self
        webDeckItem.image = menuIcon(named: "safari")

        if !accessibility.isTrusted {
            menu.addItem(
                withTitle: "Request Accessibility Access",
                action: #selector(requestAccessibilityAccess),
                keyEquivalent: ""
            ).target = self
        }

        let diagnosticsItem = menu.addItem(
            withTitle: "Diagnostics",
            action: #selector(showDiagnostics),
            keyEquivalent: "d"
        )
        diagnosticsItem.target = self
        diagnosticsItem.image = menuIcon(named: "stethoscope")
        menu.addItem(.separator())
        let quitItem = menu.addItem(
            withTitle: "Quit",
            action: #selector(quit),
            keyEquivalent: "q"
        )
        quitItem.target = self
        quitItem.image = menuIcon(named: "xmark.rectangle")

        statusItem?.menu = menu
    }

    private func menuHeaderView(version: String) -> NSView {
        let view = NSView(
            frame: NSRect(x: 0, y: 0, width: 260, height: 32)
        )
        let title = NSTextField(
            labelWithString: runtimeProfile.displayName
        )
        title.font = .boldSystemFont(ofSize: 14)
        title.textColor = .white
        let versionLabel = NSTextField(labelWithString: version)
        versionLabel.font = .systemFont(ofSize: 9)
        versionLabel.textColor = .white

        let stack = NSStackView(views: [title, versionLabel])
        stack.orientation = .horizontal
        stack.alignment = .firstBaseline
        stack.spacing = 3
        stack.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: 16),
            stack.centerYAnchor.constraint(equalTo: view.centerYAnchor),
        ])
        return view
    }

    private func menuIcon(named symbolName: String) -> NSImage? {
        guard
            let image = NSImage(
                systemSymbolName: symbolName,
                accessibilityDescription: nil
            )
        else {
            return nil
        }
        image.isTemplate = true
        image.size = NSSize(width: 16, height: 16)
        return image
    }

    private func showExclusiveLaunchFailure(_ details: String) {
        NSApp.activate(ignoringOtherApps: true)
        let alert = NSAlert()
        alert.messageText = "elChango is already running"
        alert.informativeText = """
            \(details). Stable and debug builds share port 8765 and cannot run at \
            the same time.
            """
        alert.alertStyle = .warning
        alert.addButton(withTitle: "Quit")
        alert.runModal()
        NSApp.terminate(nil)
    }

    @objc
    private func openWebDeck() {
        guard let service else { return }
        Task {
            guard let url = await service.webDeckURL() else { return }
            NSWorkspace.shared.open(url)
        }
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
        let version =
            Bundle.main.object(
                forInfoDictionaryKey: "CFBundleShortVersionString"
            ) as? String ?? "unknown"
        switch serviceState {
        case .starting:
            serviceDetails = "starting"
        case .running:
            serviceDetails = "running"
        case .failed(let reason):
            serviceDetails = "unavailable: \(reason)"
        }

        return """
            Version: \(version)
            Service: \(serviceDetails)
            Endpoint: http://127.0.0.1:\(LoopbackService.defaultPort)
            Accessibility: \(accessibility.isTrusted ? "granted" : "not granted")

            Cursor: \(providerStatuses["cursor"] ?? "unknown")
            Claude Code: \(providerStatuses["claude-code"] ?? "unknown")
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
