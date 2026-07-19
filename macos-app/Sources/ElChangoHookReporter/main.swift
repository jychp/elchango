import Foundation

@main
struct ElChangoHookReporter {
    static func main() async {
        if CommandLine.arguments.contains("--help") {
            print(
                "Usage: elChangoHookReporter --provider cursor|claude-code"
            )
            return
        }
        defer { print("{}") }
        guard CommandLine.arguments.count == 3,
            CommandLine.arguments[1] == "--provider",
            let provider = Provider(rawValue: CommandLine.arguments[2])
        else {
            return
        }

        let input = FileHandle.standardInput.readDataToEndOfFile()
        guard !input.isEmpty, input.count <= 65_536,
            let payload = try? JSONSerialization.jsonObject(with: input)
                as? [String: Any]
        else {
            return
        }
        let sanitized = provider.sanitize(payload)
        guard JSONSerialization.isValidJSONObject(sanitized),
            let body = try? JSONSerialization.data(withJSONObject: sanitized),
            let url = URL(
                string: "http://127.0.0.1:8765/api/hooks/\(provider.rawValue)"
            )
        else {
            return
        }

        var request = URLRequest(url: url, timeoutInterval: 0.8)
        request.httpMethod = "POST"
        request.setValue(
            "application/json",
            forHTTPHeaderField: "Content-Type"
        )
        request.httpBody = body
        _ = try? await URLSession.shared.data(for: request)
    }
}

private enum Provider: String {
    case cursor
    case claudeCode = "claude-code"

    func sanitize(_ payload: [String: Any]) -> [String: String] {
        let keys: Set<String>
        switch self {
        case .cursor:
            keys = [
                "hook_event_name",
                "conversation_id",
                "generation_id",
                "composer_mode",
                "status",
                "subagent_id",
            ]
        case .claudeCode:
            keys = [
                "hook_event_name",
                "session_id",
                "cwd",
                "transcript_path",
                "notification_type",
                "tool_name",
                "prompt_id",
                "permission_mode",
                "trigger",
                "source",
                "agent_id",
            ]
        }
        return payload.reduce(into: [:]) { result, element in
            guard keys.contains(element.key),
                let value = element.value as? String,
                !value.isEmpty
            else {
                return
            }
            result[element.key] = value
        }
    }
}
