import Foundation
import SQLite3

enum SQLiteReadError: LocalizedError {
    case openFailed(String)
    case queryFailed(String)
    case invalidText
    case invalidType(expected: String, actual: Int32)
    case invalidBoolean(Int64)
    case databaseIsWritable

    var errorDescription: String? {
        switch self {
        case .openFailed(let message):
            "SQLite open failed: \(message)"
        case .queryFailed(let message):
            "SQLite query failed: \(message)"
        case .invalidText:
            "SQLite returned invalid UTF-8 text"
        case .invalidType(let expected, let actual):
            "SQLite expected \(expected), found type \(actual)"
        case .invalidBoolean(let value):
            "SQLite expected boolean 0 or 1, found \(value)"
        case .databaseIsWritable:
            "SQLite database unexpectedly opened as writable"
        }
    }
}

final class SQLiteReadConnection {
    private var database: OpaquePointer?

    init(url: URL) throws {
        var database: OpaquePointer?
        let flags = SQLITE_OPEN_READONLY
            | SQLITE_OPEN_URI
            | SQLITE_OPEN_PRIVATECACHE
            | SQLITE_OPEN_EXRESCODE
        let result = sqlite3_open_v2(
            url.resolvingSymlinksInPath().path,
            &database,
            flags,
            nil
        )
        guard result == SQLITE_OK, let database else {
            let message = database.map {
                String(cString: sqlite3_errmsg($0))
            } ?? "unknown error"
            if let database {
                sqlite3_close(database)
            }
            throw SQLiteReadError.openFailed(message)
        }
        self.database = database
        sqlite3_extended_result_codes(database, 1)
        sqlite3_busy_timeout(database, 2_000)
        try execute("PRAGMA query_only=ON")
        try execute("PRAGMA trusted_schema=OFF")
        guard sqlite3_db_readonly(database, "main") == 1 else {
            throw SQLiteReadError.databaseIsWritable
        }
        try execute("BEGIN DEFERRED TRANSACTION")
    }

    deinit {
        if let database {
            sqlite3_exec(database, "ROLLBACK", nil, nil, nil)
            sqlite3_close_v2(database)
        }
    }

    func commit() throws {
        try execute("COMMIT")
    }

    func execute(_ sql: String) throws {
        guard let database else {
            throw SQLiteReadError.queryFailed("database is closed")
        }
        var errorMessage: UnsafeMutablePointer<CChar>?
        let result = sqlite3_exec(
            database,
            sql,
            nil,
            nil,
            &errorMessage
        )
        guard result == SQLITE_OK else {
            let message = errorMessage.map { String(cString: $0) }
                ?? String(cString: sqlite3_errmsg(database))
            sqlite3_free(errorMessage)
            throw SQLiteReadError.queryFailed(message)
        }
    }

    func withRows<Result>(
        _ sql: String,
        bindings: [String] = [],
        _ body: (SQLiteRow) throws -> Result?
    ) throws -> [Result] {
        guard let database else {
            throw SQLiteReadError.queryFailed("database is closed")
        }
        var statement: OpaquePointer?
        guard sqlite3_prepare_v2(
            database,
            sql,
            -1,
            &statement,
            nil
        ) == SQLITE_OK, let statement else {
            throw SQLiteReadError.queryFailed(
                String(cString: sqlite3_errmsg(database))
            )
        }
        defer { sqlite3_finalize(statement) }

        for (index, value) in bindings.enumerated() {
            let result = value.withCString { pointer in
                sqlite3_bind_text(
                    statement,
                    Int32(index + 1),
                    pointer,
                    -1,
                    Self.transientDestructor
                )
            }
            guard result == SQLITE_OK else {
                throw SQLiteReadError.queryFailed(
                    String(cString: sqlite3_errmsg(database))
                )
            }
        }

        var results: [Result] = []
        while true {
            switch sqlite3_step(statement) {
            case SQLITE_ROW:
                if let result = try body(SQLiteRow(statement: statement)) {
                    results.append(result)
                }
            case SQLITE_DONE:
                return results
            default:
                throw SQLiteReadError.queryFailed(
                    String(cString: sqlite3_errmsg(database))
                )
            }
        }
    }

    private static let transientDestructor = unsafeBitCast(
        -1,
        to: sqlite3_destructor_type.self
    )
}

struct SQLiteRow {
    fileprivate let statement: OpaquePointer

    func text(
        _ index: Int32,
        allowBlob: Bool = false
    ) throws -> String? {
        let type = sqlite3_column_type(statement, index)
        guard type != SQLITE_NULL else {
            return nil
        }
        guard type == SQLITE_TEXT || (allowBlob && type == SQLITE_BLOB) else {
            throw SQLiteReadError.invalidType(
                expected: allowBlob ? "TEXT or BLOB" : "TEXT",
                actual: type
            )
        }
        guard let pointer = sqlite3_column_text(statement, index) else {
            throw SQLiteReadError.invalidText
        }
        let count = Int(sqlite3_column_bytes(statement, index))
        let data = Data(bytes: pointer, count: count)
        guard let text = String(data: data, encoding: .utf8) else {
            throw SQLiteReadError.invalidText
        }
        return text
    }

    func integer(_ index: Int32) -> Int64? {
        guard sqlite3_column_type(statement, index) == SQLITE_INTEGER else {
            return nil
        }
        return sqlite3_column_int64(statement, index)
    }

    func boolean(_ index: Int32) throws -> Bool {
        let type = sqlite3_column_type(statement, index)
        guard type == SQLITE_INTEGER else {
            throw SQLiteReadError.invalidType(
                expected: "INTEGER boolean",
                actual: type
            )
        }
        let value = sqlite3_column_int64(statement, index)
        guard value == 0 || value == 1 else {
            throw SQLiteReadError.invalidBoolean(value)
        }
        return value == 1
    }
}
