import Darwin
import Foundation

public struct DeckPreferences: Equatable, Sendable {
    public static let defaultActionSlots: [CommandID] = [
        .accept,
        .commitPush,
        .createPR,
    ]

    public let sessionIcons: [String: DeckIcon]
    public let actionSlots: [CommandID]

    public init(
        sessionIcons: [String: DeckIcon],
        actionSlots: [CommandID]
    ) {
        self.sessionIcons = sessionIcons
        self.actionSlots = actionSlots
    }

    public static let defaults = DeckPreferences(
        sessionIcons: [:],
        actionSlots: defaultActionSlots
    )
}

public enum PreferencesStoreError: LocalizedError, Equatable {
    case invalidPreferences(String)
    case unsupportedSchema(URL)
    case invalidSessionIcons(URL)
    case invalidActionSlots(URL)
    case persistenceFailed(String)

    public var errorDescription: String? {
        switch self {
        case .invalidPreferences(let details):
            details
        case .unsupportedSchema(let path):
            "\(path.path): unsupported preferences schema"
        case .invalidSessionIcons(let path):
            "\(path.path): invalid session icon preferences"
        case .invalidActionSlots(let path):
            "\(path.path): invalid action slot preferences"
        case .persistenceFailed(let details):
            details
        }
    }
}

public actor PreferencesStore {
    public static let version = 1

    public static var defaultURL: URL {
        FileManager.default.urls(
            for: .applicationSupportDirectory,
            in: .userDomainMask
        )[0]
        .appendingPathComponent("elChango", isDirectory: true)
        .appendingPathComponent("preferences.json", isDirectory: false)
    }

    public nonisolated let url: URL
    private var preferences: DeckPreferences

    public init(url: URL = PreferencesStore.defaultURL) throws {
        self.url = url
        self.preferences = try Self.load(from: url)
    }

    public func snapshot() -> DeckPreferences {
        preferences
    }

    public func setSessionIcon(
        sessionID: String,
        icon: DeckIcon
    ) throws {
        guard !sessionID.isEmpty,
            DeckIcon.personalizationOptions.contains(icon)
        else {
            throw PreferencesStoreError.invalidSessionIcons(url)
        }
        var icons = preferences.sessionIcons
        icons[sessionID] = icon
        try replace(
            with: DeckPreferences(
                sessionIcons: icons,
                actionSlots: preferences.actionSlots
            )
        )
    }

    public func setActionSlot(
        index: Int,
        commandID: CommandID
    ) throws {
        guard 0..<3 ~= index else {
            throw PreferencesStoreError.invalidActionSlots(url)
        }
        var slots = preferences.actionSlots
        slots[index] = commandID
        try replace(
            with: DeckPreferences(
                sessionIcons: preferences.sessionIcons,
                actionSlots: slots
            )
        )
    }

    private func replace(with updated: DeckPreferences) throws {
        do {
            try Self.persist(updated, to: url)
        } catch let error as PreferencesStoreError {
            throw error
        } catch {
            throw PreferencesStoreError.persistenceFailed(
                "\(url.path): could not save preferences: \(error)"
            )
        }
        preferences = updated
    }

    private struct Payload: Codable {
        let version: Int
        let sessionIcons: [String: String]
        let actionSlots: [String]

        private enum CodingKeys: String, CodingKey {
            case version
            case sessionIcons = "session_icons"
            case actionSlots = "action_slots"
        }
    }

    private static func load(from url: URL) throws -> DeckPreferences {
        guard FileManager.default.fileExists(atPath: url.path) else {
            return .defaults
        }

        let payload: Payload
        do {
            payload = try JSONDecoder().decode(
                Payload.self,
                from: Data(contentsOf: url)
            )
        } catch {
            throw PreferencesStoreError.invalidPreferences(
                "\(url.path): invalid preferences: \(error)"
            )
        }

        guard payload.version == version else {
            throw PreferencesStoreError.unsupportedSchema(url)
        }

        var icons: [String: DeckIcon] = [:]
        for (sessionID, rawIcon) in payload.sessionIcons {
            guard !sessionID.isEmpty,
                let icon = DeckIcon(rawValue: rawIcon),
                DeckIcon.personalizationOptions.contains(icon)
            else {
                throw PreferencesStoreError.invalidSessionIcons(url)
            }
            icons[sessionID] = icon
        }

        guard payload.actionSlots.count == 3 else {
            throw PreferencesStoreError.invalidActionSlots(url)
        }
        let slots = try payload.actionSlots.map { rawValue in
            guard let command = CommandID(rawValue: rawValue) else {
                throw PreferencesStoreError.invalidActionSlots(url)
            }
            return command
        }

        return DeckPreferences(
            sessionIcons: icons,
            actionSlots: slots
        )
    }

    private static func persist(
        _ preferences: DeckPreferences,
        to url: URL
    ) throws {
        let directory = url.deletingLastPathComponent()
        try FileManager.default.createDirectory(
            at: directory,
            withIntermediateDirectories: true
        )

        let payload = Payload(
            version: version,
            sessionIcons: preferences.sessionIcons.mapValues(\.rawValue),
            actionSlots: preferences.actionSlots.map(\.rawValue)
        )
        let data = pythonCompatibleData(for: payload)

        let temporaryURL = directory.appendingPathComponent(
            ".\(url.lastPathComponent).\(UUID().uuidString).tmp",
            isDirectory: false
        )
        let descriptor = temporaryURL.path.withCString { path in
            Darwin.open(
                path,
                O_WRONLY | O_CREAT | O_EXCL,
                S_IRUSR | S_IWUSR
            )
        }
        guard descriptor >= 0 else {
            throw PreferencesStoreError.persistenceFailed(
                "\(url.path): could not create temporary preferences"
            )
        }

        do {
            let handle = FileHandle(
                fileDescriptor: descriptor,
                closeOnDealloc: true
            )
            try handle.write(contentsOf: data)
            try handle.synchronize()
            try handle.close()

            let result = temporaryURL.path.withCString { source in
                url.path.withCString { destination in
                    Darwin.rename(source, destination)
                }
            }
            guard result == 0 else {
                let message = String(cString: strerror(errno))
                throw PreferencesStoreError.persistenceFailed(
                    "\(url.path): could not replace preferences: \(message)"
                )
            }
        } catch {
            try? FileManager.default.removeItem(at: temporaryURL)
            throw error
        }
    }

    private static func pythonCompatibleData(for payload: Payload) -> Data {
        var lines = [
            "{",
            "  \"action_slots\": [",
        ]
        for (index, command) in payload.actionSlots.enumerated() {
            let comma = index + 1 < payload.actionSlots.count ? "," : ""
            lines.append("    \(pythonJSONString(command))\(comma)")
        }
        lines.append("  ],")

        if payload.sessionIcons.isEmpty {
            lines.append("  \"session_icons\": {},")
        } else {
            lines.append("  \"session_icons\": {")
            let sessionIDs = payload.sessionIcons.keys.sorted(
                by: pythonStringLessThan
            )
            for (index, sessionID) in sessionIDs.enumerated() {
                let comma = index + 1 < sessionIDs.count ? "," : ""
                let icon = payload.sessionIcons[sessionID]!
                lines.append(
                    "    \(pythonJSONString(sessionID)): "
                        + "\(pythonJSONString(icon))\(comma)"
                )
            }
            lines.append("  },")
        }
        lines.append("  \"version\": \(payload.version)")
        lines.append("}")
        return Data((lines.joined(separator: "\n") + "\n").utf8)
    }

    private static func pythonJSONString(_ value: String) -> String {
        var result = "\""
        for scalar in value.unicodeScalars {
            switch scalar.value {
            case 0x08:
                result += "\\b"
            case 0x09:
                result += "\\t"
            case 0x0A:
                result += "\\n"
            case 0x0C:
                result += "\\f"
            case 0x0D:
                result += "\\r"
            case 0x22:
                result += "\\\""
            case 0x5C:
                result += "\\\\"
            case 0x00...0x1F:
                result += String(format: "\\u%04x", scalar.value)
            case 0x20...0x7E:
                result.append(Character(scalar))
            case 0x7F...0xFFFF:
                result += String(format: "\\u%04x", scalar.value)
            default:
                let codePoint = scalar.value - 0x1_0000
                let high = 0xD800 + (codePoint >> 10)
                let low = 0xDC00 + (codePoint & 0x3FF)
                result += String(format: "\\u%04x\\u%04x", high, low)
            }
        }
        result += "\""
        return result
    }

    private static func pythonStringLessThan(
        _ lhs: String,
        _ rhs: String
    ) -> Bool {
        let left = lhs.unicodeScalars.map(\.value)
        let right = rhs.unicodeScalars.map(\.value)
        for (leftValue, rightValue) in zip(left, right) {
            if leftValue != rightValue {
                return leftValue < rightValue
            }
        }
        return left.count < right.count
    }
}
