import Darwin
import Foundation

public enum ServiceLeaseError: LocalizedError, Equatable {
    case alreadyRunning(String?)
    case system(String)

    public var errorDescription: String? {
        switch self {
        case .alreadyRunning(let holder):
            if let holder, !holder.isEmpty {
                "\(holder) already owns the shared elChango service"
            } else {
                "Another elChango build already owns the shared service"
            }
        case .system(let details):
            "Shared elChango service lock failed: \(details)"
        }
    }
}

public final class ServiceLease: @unchecked Sendable {
    public static var defaultURL: URL {
        FileManager.default.urls(
            for: .applicationSupportDirectory,
            in: .userDomainMask
        )[0]
        .appendingPathComponent("elChango", isDirectory: true)
        .appendingPathComponent("service.lock", isDirectory: false)
    }

    private let descriptor: Int32

    public init(
        holder: String,
        url: URL = ServiceLease.defaultURL
    ) throws {
        let directory = url.deletingLastPathComponent()
        try FileManager.default.createDirectory(
            at: directory,
            withIntermediateDirectories: true,
            attributes: [.posixPermissions: 0o700]
        )
        guard chmod(directory.path, S_IRWXU) == 0 else {
            throw Self.systemError(path: directory.path)
        }

        let descriptor = open(
            url.path,
            O_RDWR | O_CREAT | O_NOFOLLOW,
            S_IRUSR | S_IWUSR
        )
        guard descriptor >= 0 else {
            throw Self.systemError(path: url.path)
        }
        guard flock(descriptor, LOCK_EX | LOCK_NB) == 0 else {
            let errorCode = errno
            let existingHolder = Self.readHolder(descriptor: descriptor)
            close(descriptor)
            if errorCode == EWOULDBLOCK {
                throw ServiceLeaseError.alreadyRunning(existingHolder)
            }
            throw ServiceLeaseError.system(
                "\(url.path): \(String(cString: strerror(errorCode)))"
            )
        }

        do {
            try Self.writeHolder(
                "\(holder) (pid \(getpid()))\n",
                descriptor: descriptor,
                path: url.path
            )
        } catch {
            flock(descriptor, LOCK_UN)
            close(descriptor)
            throw error
        }
        self.descriptor = descriptor
    }

    deinit {
        flock(descriptor, LOCK_UN)
        close(descriptor)
    }

    private static func readHolder(descriptor: Int32) -> String? {
        guard lseek(descriptor, 0, SEEK_SET) >= 0 else { return nil }
        var bytes = [UInt8](repeating: 0, count: 256)
        let count = bytes.withUnsafeMutableBytes { buffer in
            Darwin.read(descriptor, buffer.baseAddress, buffer.count)
        }
        guard count > 0 else { return nil }
        return String(decoding: bytes.prefix(count), as: UTF8.self)
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private static func writeHolder(
        _ holder: String,
        descriptor: Int32,
        path: String
    ) throws {
        guard ftruncate(descriptor, 0) == 0,
            lseek(descriptor, 0, SEEK_SET) >= 0
        else {
            throw systemError(path: path)
        }
        let data = Data(holder.utf8)
        try data.withUnsafeBytes { buffer in
            guard let baseAddress = buffer.baseAddress else { return }
            var offset = 0
            while offset < buffer.count {
                let written = Darwin.write(
                    descriptor,
                    baseAddress.advanced(by: offset),
                    buffer.count - offset
                )
                if written == -1 && errno == EINTR {
                    continue
                }
                guard written > 0 else {
                    throw systemError(path: path)
                }
                offset += written
            }
        }
        guard fsync(descriptor) == 0 else {
            throw systemError(path: path)
        }
    }

    private static func systemError(path: String) -> ServiceLeaseError {
        .system("\(path): \(String(cString: strerror(errno)))")
    }
}
