// Where everything is: what the app carries, and what this Mac built.
import Foundation

enum Paths {
    static let title = "Theme Park World"
    /// A portable.txt beside the app: the game, its saves and logs in data/
    /// beside it, not in Application Support and Logs.
    static let portable: URL? = {
        let beside = Bundle.main.bundleURL.deletingLastPathComponent()
        guard exists(beside.appendingPathComponent("portable.txt")) else { return nil }
        return beside.appendingPathComponent("data")
    }()
    static let home: URL = {
        if let own = ProcessInfo.processInfo.environment["TPW_HOME"], !own.isEmpty {
            return URL(fileURLWithPath: own)
        }
        if let portable { return portable }
        return FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/\(title)")
    }()
    static let game = home.appendingPathComponent("game")
    static let library = home.appendingPathComponent("build/host/libtpw.dylib")
    static let firstLaunch = home.appendingPathComponent(".launched")

    static let logs = portable?.appendingPathComponent("logs")
        ?? FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Logs/\(title)")
    static let gameLog = logs.appendingPathComponent("tpw.log")
    static let installLog = logs.appendingPathComponent("install.log")

    // What the app carries (app/build-app.sh).
    static let resources = Bundle.main.resourceURL!
    static let python = resources.appendingPathComponent("toolchain/python/bin/python3")
    static let installer = resources.appendingPathComponent("source/tpw/tools/install/install.py")
    static let pcrecomp = resources.appendingPathComponent("source/pcrecomp")
    static let frameworks = Bundle.main.privateFrameworksURL!

    static func exists(_ url: URL) -> Bool { FileManager.default.fileExists(atPath: url.path) }
}
