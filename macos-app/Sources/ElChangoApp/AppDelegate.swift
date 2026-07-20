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
    private let streamDeckPluginBundleIdentifier = "com.elgato.StreamDeck"
    private let cursorBundleIdentifier = "com.todesktop.230313mzl4w4u92"
    private let codexBundleIdentifier = "com.openai.codex"
    private var lastStreamDeckInstallFailure: String?
    private var lastClaudeInstallFailure: String?
    private var lastCodexInstallFailure: String?
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
                uniqueKeysWithValues: ["cursor", "claude-code", "codex"].map { id in
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
                self.service = nil
                serviceLease = nil
                rebuildMenu()
            }
        } catch {
            serviceState = .failed(error.localizedDescription)
            service = nil
            serviceLease = nil
            rebuildMenu()
            if case ServiceLeaseError.alreadyRunning = error {
                showExclusiveLaunchFailure(error.localizedDescription)
            }
        }
    }

    private func rebuildMenu() {
        let menu = NSMenu()

        let version = appVersion
        let titleItem = NSMenuItem(
            title: "",
            action: nil,
            keyEquivalent: ""
        )
        titleItem.view = menuHeaderView(version: version)
        menu.addItem(titleItem)

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

        switch streamDeckPluginState {
        case .pluginMissing:
            let installItem = menu.addItem(
                withTitle: "Install Stream Deck Plugin",
                action: #selector(installStreamDeckPlugin),
                keyEquivalent: ""
            )
            installItem.target = self
            installItem.image = menuIcon(named: "square.and.arrow.down")
        case .mismatched:
            let updateItem = menu.addItem(
                withTitle: "Update Stream Deck Plugin",
                action: #selector(installStreamDeckPlugin),
                keyEquivalent: ""
            )
            updateItem.target = self
            updateItem.image = menuIcon(named: "arrow.down.circle")
        case .streamDeckNotDetected, .matching, .malformed, .unreadable:
            break
        }

        switch claudeCodePluginState {
        case .pluginMissing:
            let installItem = menu.addItem(
                withTitle: "Install Claude Code Plugin",
                action: #selector(installClaudeCodePlugin),
                keyEquivalent: ""
            )
            installItem.target = self
            installItem.image = menuIcon(named: "square.and.arrow.down")
        case .mismatched:
            let updateItem = menu.addItem(
                withTitle: "Update Claude Code Plugin",
                action: #selector(installClaudeCodePlugin),
                keyEquivalent: ""
            )
            updateItem.target = self
            updateItem.image = menuIcon(named: "arrow.down.circle")
        case .cliNotAvailable, .matching, .malformed, .unreadable:
            break
        }

        switch codexPluginState {
        case .pluginMissing:
            let installItem = menu.addItem(
                withTitle: "Install Codex Plugin",
                action: #selector(installCodexPlugin),
                keyEquivalent: ""
            )
            installItem.target = self
            installItem.image = menuIcon(named: "square.and.arrow.down")
        case .mismatched:
            let updateItem = menu.addItem(
                withTitle: "Update Codex Plugin",
                action: #selector(installCodexPlugin),
                keyEquivalent: ""
            )
            updateItem.target = self
            updateItem.image = menuIcon(named: "arrow.down.circle")
        case .codexNotDetected, .matching, .managed, .malformed, .unreadable:
            break
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
    private func installStreamDeckPlugin() {
        guard let url = Self.bundledStreamDeckPluginURL else {
            lastStreamDeckInstallFailure =
                "the bundled Stream Deck plugin could not be found"
            rebuildMenu()
            return
        }
        // Hand the artifact to the system association for .streamDeckPlugin so
        // Stream Deck owns confirmation and installation.
        let opened = NSWorkspace.shared.open(url)
        lastStreamDeckInstallFailure =
            opened
            ? nil
            : "macOS could not open the Stream Deck plugin installer"
        rebuildMenu()
    }

    @objc
    private func installClaudeCodePlugin() {
        guard let executable = ClaudeCLILocator().locate() else {
            lastClaudeInstallFailure = "the claude CLI could not be found"
            rebuildMenu()
            return
        }
        // Run the official plugin commands off the main thread so the menu stays
        // responsive; report the outcome through Diagnostics.
        Task {
            let result = await Task.detached {
                await ClaudePluginInstaller().install(
                    claudeExecutable: executable
                )
            }.value
            switch result {
            case .success:
                lastClaudeInstallFailure = nil
            case .failure(let error):
                lastClaudeInstallFailure = error.localizedDescription
            }
            rebuildMenu()
        }
    }

    @objc
    private func installCodexPlugin() {
        guard let executable = CodexCLILocator().locate() else {
            lastCodexInstallFailure = "the codex CLI could not be found"
            rebuildMenu()
            return
        }
        // Run the official plugin commands off the main thread so the menu stays
        // responsive; report the outcome through Diagnostics.
        Task {
            let result = await Task.detached {
                await CodexPluginInstaller().install(
                    codexExecutable: executable
                )
            }.value
            switch result {
            case .success:
                lastCodexInstallFailure = nil
            case .failure(let error):
                lastCodexInstallFailure = error.localizedDescription
            }
            rebuildMenu()
        }
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
        let version = appVersion
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

            Stream Deck: \(streamDeckDiagnosticsDetail)
            Claude: \(claudeDiagnosticsDetail)
            Cursor: \(cursorDiagnosticsDetail)
            Codex: \(codexDiagnosticsDetail)
            """
    }

    private var claudeDiagnosticsDetail: String {
        if let unavailable = unavailableProviderDetail(for: "claude-code") {
            return unavailable
        }
        let stateDetail: String
        switch claudeCodePluginState {
        case .cliNotAvailable:
            stateDetail = "claude CLI not found"
        case .pluginMissing:
            stateDetail = "not installed"
        case .matching(let installedVersion):
            stateDetail = installedVersion
        case .mismatched(let installedVersion, let expectedVersion):
            stateDetail = "\(installedVersion) -> \(expectedVersion)"
        case .malformed:
            stateDetail = "malformed"
        case .unreadable(let reason):
            stateDetail = "unreadable: \(reason)"
        }
        guard let failure = lastClaudeInstallFailure else {
            return stateDetail
        }
        return "\(stateDetail); last install failed: \(failure)"
    }

    private var cursorDiagnosticsDetail: String {
        if let unavailable = unavailableProviderDetail(for: "cursor") {
            return unavailable
        }
        switch cursorPluginState {
        case .cursorNotDetected:
            return "Cursor not detected"
        case .pluginMissing:
            return "not installed"
        case .matching(let installedVersion):
            return installedVersion
        case .mismatched(let installedVersion, let expectedVersion):
            return "\(installedVersion) -> \(expectedVersion)"
        case .malformed:
            return "malformed"
        case .managed(let version):
            if let version {
                return "manual (\(version))"
            }
            return "manual"
        case .unreadable(let reason):
            return "unreadable: \(reason)"
        }
    }

    private var codexDiagnosticsDetail: String {
        if let unavailable = unavailableProviderDetail(for: "codex") {
            return unavailable
        }
        let stateDetail: String
        switch codexPluginState {
        case .codexNotDetected:
            stateDetail = "Codex not detected"
        case .pluginMissing:
            stateDetail = "not installed"
        case .matching(let installedVersion):
            stateDetail = installedVersion
        case .mismatched(let installedVersion, let expectedVersion):
            stateDetail = "\(installedVersion) -> \(expectedVersion)"
        case .malformed:
            stateDetail = "malformed"
        case .managed(let version):
            if let version {
                stateDetail = "manual (\(version))"
            } else {
                stateDetail = "manual"
            }
        case .unreadable(let reason):
            stateDetail = "unreadable: \(reason)"
        }
        guard let failure = lastCodexInstallFailure else {
            return stateDetail
        }
        return "\(stateDetail); last install failed: \(failure)"
    }

    private var streamDeckDiagnosticsDetail: String {
        let stateDetail: String
        switch streamDeckPluginState {
        case .streamDeckNotDetected:
            stateDetail = "Stream Deck not detected"
        case .pluginMissing:
            stateDetail = "not installed"
        case .matching(let installedVersion):
            stateDetail = installedVersion
        case .mismatched(let installedVersion, let bundledVersion):
            stateDetail = "\(installedVersion) -> \(bundledVersion)"
        case .malformed:
            stateDetail = "malformed"
        case .unreadable(let reason):
            stateDetail = "unreadable: \(reason)"
        }
        guard let failure = lastStreamDeckInstallFailure else {
            return stateDetail
        }
        return "\(stateDetail); last install failed: \(failure)"
    }

    /// The provider's runtime status only when it is unavailable, so a real
    /// initialization failure is still surfaced; otherwise nil, so the line
    /// shows the plugin version instead of a redundant "enabled".
    private func unavailableProviderDetail(for providerID: String) -> String? {
        guard let status = providerStatuses[providerID],
            status.hasPrefix("unavailable")
        else {
            return nil
        }
        return status
    }

    private var appVersion: String {
        Bundle.main.object(
            forInfoDictionaryKey: "CFBundleShortVersionString"
        ) as? String ?? "unknown"
    }

    private var isStreamDeckInstalled: Bool {
        NSWorkspace.shared.urlForApplication(
            withBundleIdentifier: streamDeckPluginBundleIdentifier
        ) != nil
    }

    private var streamDeckPluginState: StreamDeckPluginState {
        let inspector = StreamDeckPluginInspector(
            bundledVersion: "\(appVersion).0"
        )
        return inspector.classify(streamDeckInstalled: isStreamDeckInstalled)
    }

    private var isCursorInstalled: Bool {
        NSWorkspace.shared.urlForApplication(
            withBundleIdentifier: cursorBundleIdentifier
        ) != nil
    }

    private var claudeCodePluginState: ClaudeCodePluginState {
        let inspector = ClaudeCodePluginInspector(expectedVersion: appVersion)
        return inspector.classify(
            cliAvailable: ClaudeCLILocator().locate() != nil
        )
    }

    private var cursorPluginState: CursorPluginState {
        let inspector = CursorPluginInspector(expectedVersion: appVersion)
        return inspector.classify(cursorInstalled: isCursorInstalled)
    }

    private var isCodexInstalled: Bool {
        NSWorkspace.shared.urlForApplication(
            withBundleIdentifier: codexBundleIdentifier
        ) != nil
    }

    private var codexPluginState: CodexPluginState {
        let inspector = CodexPluginInspector(expectedVersion: appVersion)
        return inspector.classify(codexInstalled: isCodexInstalled)
    }

    private static var bundledStreamDeckPluginURL: URL? {
        let fileManager = FileManager.default
        let resourceName = "com.jychp.elchango"
        let resourceExtension = "streamDeckPlugin"
        if let bundled = Bundle.main.url(
            forResource: resourceName,
            withExtension: resourceExtension
        ) {
            return bundled
        }

        let development = URL(
            fileURLWithPath: fileManager.currentDirectoryPath,
            isDirectory: true
        )
        .appendingPathComponent("../plugins/streamdeck", isDirectory: true)
        .appendingPathComponent(
            "\(resourceName).\(resourceExtension)",
            isDirectory: false
        )
        .standardizedFileURL
        if fileManager.fileExists(atPath: development.path) {
            return development
        }
        return nil
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
