import Foundation

// Load / atomic-save / append-log / source-path resolution for a project directory.
// Path resolution mirrors studio/src/lib/project.ts: relative sources resolve against the
// videos dir (edit/'s parent), falling back to the edit dir itself.

struct LoadedProject {
    var edlPath: String
    var dir: String
    var edl: Edl
    var sourcePaths: [String: String]   // source key -> absolute path
    var transcripts: [String: [Word]]   // source key -> words (for snapping)
    var projectMd: String?
}

enum Project {
    static func dirname(_ p: String) -> String {
        let u = URL(fileURLWithPath: p).deletingLastPathComponent()
        return u.path
    }

    static func basename(_ p: String) -> String {
        URL(fileURLWithPath: p).lastPathComponent
    }

    static func stem(_ name: String) -> String {
        URL(fileURLWithPath: name).deletingPathExtension().lastPathComponent
    }

    /// ".../my-video/edit/edl.json" -> "my-video" (skip the conventional edit/ dir).
    static func projectName(dir: String) -> String {
        let b = basename(dir)
        return b == "edit" ? (basename(dirname(dir))) : b
    }

    static func load(_ edlPath: String) throws -> LoadedProject {
        let data = try Data(contentsOf: URL(fileURLWithPath: edlPath))
        let edl = try JSONDecoder().decode(Edl.self, from: data)
        let dir = dirname(edlPath)
        let fm = FileManager.default

        // Real EDLs use relative source paths, conventionally in the videos dir (edit/'s parent).
        let videosDir = basename(dir) == "edit" ? dirname(dir) : dir
        var sourcePaths: [String: String] = [:]
        for (key, p) in edl.sources {
            if p.hasPrefix("/") { sourcePaths[key] = p; continue }
            let candidates = ["\(videosDir)/\(p)", "\(dir)/\(p)"]
            sourcePaths[key] = candidates[0]
            for c in candidates where fm.fileExists(atPath: c) { sourcePaths[key] = c; break }
        }

        // Transcripts keyed by file stem (transcripts/sdk-hq.json -> "sdk-hq").
        var transcripts: [String: [Word]] = [:]
        let tdir = "\(dir)/transcripts"
        if let names = try? fm.contentsOfDirectory(atPath: tdir) {
            var byStem: [String: [Word]] = [:]
            for name in names where name.hasSuffix(".json") {
                if let d = try? Data(contentsOf: URL(fileURLWithPath: "\(tdir)/\(name)")),
                   let parsed = try? JSONDecoder().decode(Transcript.self, from: d) {
                    byStem[stem(name)] = parsed.words
                }
            }
            for (key, srcPath) in edl.sources {
                transcripts[key] = byStem[key] ?? byStem[stem(srcPath)]
            }
        }

        var projectMd: String?
        let mdPath = "\(dir)/project.md"
        if fm.fileExists(atPath: mdPath) {
            projectMd = try? String(contentsOfFile: mdPath, encoding: .utf8)
        }

        return LoadedProject(edlPath: edlPath, dir: dir, edl: edl,
                             sourcePaths: sourcePaths, transcripts: transcripts, projectMd: projectMd)
    }

    /// Atomic write: tmp file in the same dir, then rename over edl.json.
    static func save(_ edl: Edl, to edlPath: String) {
        let data = edl.encoded()
        let tmp = "\(dirname(edlPath))/.edl.json.tmp"
        do {
            try data.write(to: URL(fileURLWithPath: tmp))
            if rename(tmp, edlPath) != 0 {
                try data.write(to: URL(fileURLWithPath: edlPath))
            }
        } catch {
            try? data.write(to: URL(fileURLWithPath: edlPath))
        }
    }

    /// Append one JSON object to edit_log.jsonl. Never rewrites old lines.
    static func appendLog(dir: String, op: String, fields: [String: Any]) {
        let ts = iso8601Now()
        var obj: [String: Any] = ["ts": ts, "op": op]
        for (k, v) in fields { obj[k] = v }
        guard let json = try? JSONSerialization.data(withJSONObject: obj, options: [.sortedKeys, .withoutEscapingSlashes]) else { return }
        var line = json
        line.append(0x0A)  // newline
        let path = "\(dir)/edit_log.jsonl"
        let url = URL(fileURLWithPath: path)
        if let handle = try? FileHandle(forWritingTo: url) {
            handle.seekToEndOfFile()
            handle.write(line)
            try? handle.close()
        } else {
            try? line.write(to: url)
        }
    }

    static func iso8601Now() -> String {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = TimeZone(identifier: "UTC")
        f.dateFormat = "yyyy-MM-dd'T'HH:mm:ss'Z'"
        return f.string(from: Date())
    }
}

/// Watches edl.json for external (agent) writes via a DispatchSource. Atomic renames
/// invalidate the fd, so we re-arm on rename/delete.
final class FileWatcher {
    private let path: String
    private let onChange: () -> Void
    private let queue = DispatchQueue(label: "video-use.studio.edl-watch")
    private var source: DispatchSourceFileSystemObject?
    private var fd: Int32 = -1

    init(path: String, onChange: @escaping () -> Void) {
        self.path = path
        self.onChange = onChange
        arm()
    }

    private func arm() {
        fd = open(path, O_EVTONLY)
        guard fd >= 0 else {
            queue.asyncAfter(deadline: .now() + 0.3) { [weak self] in self?.arm() }
            return
        }
        let s = DispatchSource.makeFileSystemObjectSource(
            fileDescriptor: fd,
            eventMask: [.write, .rename, .delete, .extend],
            queue: queue)
        s.setEventHandler { [weak self] in
            guard let self else { return }
            let flags = s.data
            self.onChange()
            if flags.contains(.rename) || flags.contains(.delete) { self.rearm() }
        }
        s.setCancelHandler { [weak self] in
            if let fd = self?.fd, fd >= 0 { close(fd) }
            self?.fd = -1
        }
        source = s
        s.resume()
    }

    private func rearm() {
        source?.cancel()
        source = nil
        queue.asyncAfter(deadline: .now() + 0.1) { [weak self] in self?.arm() }
    }

    func stop() {
        source?.cancel()
        source = nil
    }
}
