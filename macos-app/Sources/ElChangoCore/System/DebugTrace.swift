#if ELCHANGO_DEBUG_PROFILE
    import OSLog
#endif

public enum DebugTrace {
    @inlinable
    @inline(__always)
    public static func emit(
        _ category: String,
        _ phase: String
    ) {
        #if ELCHANGO_DEBUG_PROFILE
            Logger(
                subsystem: "com.jychp.elchango.debug",
                category: category
            ).notice("\(phase, privacy: .public)")
        #endif
    }

    @inlinable
    @inline(__always)
    public static func failure(
        _ category: String,
        _ phase: String,
        error: any Error
    ) {
        #if ELCHANGO_DEBUG_PROFILE
            Logger(
                subsystem: "com.jychp.elchango.debug",
                category: category
            ).error(
                "\(phase, privacy: .public): \(error.localizedDescription, privacy: .public)"
            )
        #endif
    }
}
