import Foundation

enum OrderedJSON {
    case object([(String, OrderedJSON)])
    case array([OrderedJSON])
    case string(String)
    case scalar(Any)

    init(data: Data) throws {
        guard let text = String(data: data, encoding: .utf8) else {
            throw OrderedJSONError.invalidUTF8
        }
        var parser = OrderedJSONParser(text)
        self = try parser.parse()
    }

    subscript(key: String) -> OrderedJSON? {
        guard case .object(let entries) = self else { return nil }
        return entries.first { $0.0 == key }?.1
    }

    var stringValue: String? {
        guard case .string(let value) = self else { return nil }
        return value
    }

    var stringArray: [String]? {
        guard case .array(let values) = self else { return nil }
        let strings = values.compactMap(\.stringValue)
        return strings.count == values.count ? strings : nil
    }

    var arrayValues: [OrderedJSON]? {
        guard case .array(let values) = self else { return nil }
        return values
    }

    var objectEntries: [(String, OrderedJSON)]? {
        guard case .object(let entries) = self else { return nil }
        return entries
    }
}

enum OrderedJSONError: LocalizedError {
    case invalidUTF8
    case invalidJSON(String)

    var errorDescription: String? {
        switch self {
        case .invalidUTF8:
            "JSON is not valid UTF-8"
        case .invalidJSON(let message):
            "invalid JSON: \(message)"
        }
    }
}

private struct OrderedJSONParser {
    private let text: String
    private var index: String.Index

    init(_ text: String) {
        self.text = text
        index = text.startIndex
    }

    mutating func parse() throws -> OrderedJSON {
        skipWhitespace()
        let value = try parseValue()
        skipWhitespace()
        guard index == text.endIndex else {
            throw error("trailing content")
        }
        return value
    }

    private mutating func parseValue() throws -> OrderedJSON {
        guard let character = current else {
            throw error("unexpected end of input")
        }
        switch character {
        case "{":
            return try parseObject()
        case "[":
            return try parseArray()
        case "\"":
            return .string(try parseString())
        default:
            return .scalar(try parseScalar())
        }
    }

    private mutating func parseObject() throws -> OrderedJSON {
        advance()
        skipWhitespace()
        var entries: [(String, OrderedJSON)] = []
        if current == "}" {
            advance()
            return .object(entries)
        }
        while true {
            guard current == "\"" else {
                throw error("object key must be a string")
            }
            let key = try parseString()
            skipWhitespace()
            guard current == ":" else {
                throw error("missing colon after object key")
            }
            advance()
            skipWhitespace()
            entries.append((key, try parseValue()))
            skipWhitespace()
            if current == "}" {
                advance()
                return .object(entries)
            }
            guard current == "," else {
                throw error("missing object separator")
            }
            advance()
            skipWhitespace()
        }
    }

    private mutating func parseArray() throws -> OrderedJSON {
        advance()
        skipWhitespace()
        var values: [OrderedJSON] = []
        if current == "]" {
            advance()
            return .array(values)
        }
        while true {
            values.append(try parseValue())
            skipWhitespace()
            if current == "]" {
                advance()
                return .array(values)
            }
            guard current == "," else {
                throw error("missing array separator")
            }
            advance()
            skipWhitespace()
        }
    }

    private mutating func parseString() throws -> String {
        let start = index
        advance()
        var escaped = false
        while let character = current {
            advance()
            if escaped {
                escaped = false
            } else if character == "\\" {
                escaped = true
            } else if character == "\"" {
                let fragment = String(text[start..<index])
                guard let data = fragment.data(using: .utf8),
                    let decoded = try JSONSerialization.jsonObject(
                        with: data,
                        options: [.fragmentsAllowed]
                    ) as? String
                else {
                    throw error("invalid string")
                }
                return decoded
            }
        }
        throw error("unterminated string")
    }

    private mutating func parseScalar() throws -> Any {
        let start = index
        while let character = current,
            !character.isWhitespace,
            ![",", "]", "}"].contains(character)
        {
            advance()
        }
        guard start != index else {
            throw error("missing scalar")
        }
        let fragment = String(text[start..<index])
        guard let data = fragment.data(using: .utf8) else {
            throw error("invalid scalar")
        }
        do {
            return try JSONSerialization.jsonObject(
                with: data,
                options: [.fragmentsAllowed]
            )
        } catch {
            throw self.error("invalid scalar")
        }
    }

    private mutating func skipWhitespace() {
        while current?.isWhitespace == true {
            advance()
        }
    }

    private var current: Character? {
        index < text.endIndex ? text[index] : nil
    }

    private mutating func advance() {
        index = text.index(after: index)
    }

    private func error(_ message: String) -> OrderedJSONError {
        .invalidJSON(message)
    }
}
