import AVFoundation
import Combine
import Foundation
import SwiftUI

// Central observable app state. One AVPlayer for its lifetime; every committed edit or
// external EDL change rebuilds the AVMutableComposition and swaps the player item,
// preserving the playhead. All mutation happens on the main thread.
final class Store: ObservableObject {
    static let shared = Store()

    // Loaded project
    @Published var edlPath: String?
    @Published var dir: String?
    @Published var edl = Edl(version: 1, sources: [:], ranges: [], grade: nil, overlays: nil, subtitles: nil, total_duration_s: 0)
    @Published var sourcePaths: [String: String] = [:]
    @Published var transcripts: [String: [Word]] = [:]
    @Published var projectMd: String?

    // Derived / playback
    @Published var offsets: [Double] = [0]
    @Published var total: Double = 0
    @Published var selection: Int?
    @Published var playing = false
    @Published var playhead: Double = 0
    @Published var agentSynced = false          // flashes when an external write is picked up
    @Published var loadError: String?
    @Published var renderAspect: Double = 16.0 / 9.0

    // Subtitles (live, generated from transcripts + ranges)
    @Published var subtitleStyle = SubtitleStyle.default
    @Published var cues: [Cue] = []

    // Files pane
    @Published var showFiles = false
    @Published var videoFiles: [VideoFile] = []
    @Published var fileDurations: [String: Double] = [:]

    // Export sheet
    @Published var exportRunning = false
    @Published var exportOutput = ""
    @Published var showExport = false

    let player = AVPlayer()
    var sourceDurations: [String: Double] = [:]

    private var undoStack: [Edl] = []
    private var redoStack: [Edl] = []
    private var watcher: FileWatcher?
    private var lastWrittenJSON: Data?           // suppress our own writes in the watcher
    private var timeObserver: Any?
    private var statusObserver: NSKeyValueObservation?
    private var control: ControlServer?

    init() {
        let interval = CMTime(seconds: 0.05, preferredTimescale: 600)
        timeObserver = player.addPeriodicTimeObserver(forInterval: interval, queue: .main) { [weak self] time in
            guard let self else { return }
            self.playhead = max(0, min(time.seconds.isFinite ? time.seconds : 0, self.total))
            // Inspector follows playback: track the slice under the playhead while playing.
            if self.playing, let seg = VirtualTime.segmentAtOutput(self.offsets, self.playhead), seg != self.selection {
                self.selection = seg
            }
        }
        statusObserver = player.observe(\.timeControlStatus, options: [.new]) { [weak self] p, _ in
            DispatchQueue.main.async { self?.playing = (p.timeControlStatus == .playing) }
        }
    }

    // MARK: - Bootstrap

    func bootstrap() {
        if control == nil {
            control = ControlServer(
                port: 4860,
                stateProvider: { [weak self] in self?.stateJSON() ?? "{}" },
                command: { [weak self] body in self?.handleRemote(body) })
            control?.start()
        }
        if edlPath == nil,
           let arg = CommandLine.arguments.dropFirst().first(where: { $0.hasSuffix(".json") }) {
            open(path: arg)
        }
    }

    // MARK: - Loading

    func open(path: String) {
        do {
            let p = try Project.load(path)
            edlPath = p.edlPath
            dir = p.dir
            edl = p.edl
            sourcePaths = p.sourcePaths
            transcripts = p.transcripts
            projectMd = p.projectMd
            loadError = nil
            undoStack.removeAll(); redoStack.removeAll()
            selection = nil
            lastWrittenJSON = nil
            subtitleStyle = p.edl.subtitle_style ?? .default
            rebuild(preservePlayhead: false)
            discoverFiles()
            watcher?.stop()
            watcher = FileWatcher(path: path) { [weak self] in
                DispatchQueue.main.async { self?.externalChange() }
            }
        } catch {
            loadError = "Could not open \(path): \(error.localizedDescription)"
        }
    }

    func reload() {
        guard let path = edlPath else { return }
        let keep = playhead
        open(path: path)
        seek(to: keep)
    }

    private func externalChange() {
        guard let path = edlPath else { return }
        guard let data = try? Data(contentsOf: URL(fileURLWithPath: path)) else { return }
        if let last = lastWrittenJSON, last == data { return }   // our own write
        guard let fresh = try? JSONDecoder().decode(Edl.self, from: data) else { return }
        if fresh == edl { return }
        let keep = playhead
        edl = fresh
        subtitleStyle = fresh.subtitle_style ?? .default
        rebuild(preservePlayhead: true)
        seek(to: min(keep, total))
        flashSynced()
    }

    private func flashSynced() {
        withAnimation(.easeOut(duration: 0.2)) { agentSynced = true }
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.6) { [weak self] in
            withAnimation(.easeIn(duration: 0.4)) { self?.agentSynced = false }
        }
    }

    // MARK: - Composition

    private func rebuild(preservePlayhead: Bool) {
        let keep = playhead
        let result = CompositionBuilder.build(edl: edl, sourcePaths: sourcePaths)
        offsets = result.offsets
        total = result.total
        sourceDurations = result.sourceDurations
        if result.renderSize.width > 0, result.renderSize.height > 0 {
            renderAspect = result.renderSize.width / result.renderSize.height
        }
        refreshCues()

        let item = AVPlayerItem(asset: result.composition)
        item.videoComposition = result.videoComposition
        let wasPlaying = playing
        player.replaceCurrentItem(with: item)
        if preservePlayhead { seek(to: min(keep, total)) } else { playhead = 0 }
        if wasPlaying && preservePlayhead { player.play() }
    }

    // MARK: - Transport

    func togglePlay() { playing ? pause() : play() }

    func play() {
        if playhead >= total - 0.05 { seek(to: 0) }
        player.play()
        playing = true
    }

    func pause() {
        player.pause()
        playing = false
    }

    func seek(to t: Double) {
        let clamped = max(0, min(t, total))
        let time = CMTime(seconds: clamped, preferredTimescale: 600)
        player.seek(to: time, toleranceBefore: .zero, toleranceAfter: .zero)
        playhead = clamped
        // Scrubbing (incl. ruler clicks) tracks the slice under the playhead.
        selection = VirtualTime.segmentAtOutput(offsets, clamped)
    }

    /// Seek to the output-time start of a segment.
    func seekToSegment(_ i: Int) {
        guard i >= 0, i < offsets.count - 1 else { return }
        seek(to: offsets[i])
    }

    func select(_ i: Int?) {
        if let i, i >= 0, i < edl.ranges.count { selection = i } else { selection = nil }
    }

    // MARK: - Subtitles

    func refreshCues() {
        cues = SubtitleEngine.build(ranges: edl.ranges, transcripts: transcripts, style: subtitleStyle)
    }

    var currentCaption: String? {
        guard subtitleStyle.enabled else { return nil }
        return SubtitleEngine.active(cues, at: playhead)
    }

    /// Update the live subtitle style. `commit` persists to edl.json + edit_log; slider drags
    /// pass commit:false while dragging and commit:true on release.
    func updateSubtitleStyle(_ new: SubtitleStyle, commit: Bool) {
        subtitleStyle = new
        refreshCues()
        guard commit else { return }
        edl.subtitle_style = new
        persist()
        if let dir {
            Project.appendLog(dir: dir, op: "subtitle_style", fields: [
                "enabled": new.enabled,
                "size": new.size,
                "margin_v": new.margin_v,
                "uppercase": new.uppercase,
                "chunk_words": new.chunk_words,
            ])
        }
    }

    // MARK: - Files pane

    private static let videoExts: Set<String> = ["mp4", "mov", "mkv", "avi", "m4v"]

    /// Discover every video file in the videos dir (edit/'s parent), same rules as the pipeline:
    /// known video extensions, skip dotfiles and ._AppleDouble sidecars.
    func discoverFiles() {
        guard let dir else { videoFiles = []; return }
        let videosDir = Project.basename(dir) == "edit" ? Project.dirname(dir) : dir
        let fm = FileManager.default
        let names = (try? fm.contentsOfDirectory(atPath: videosDir)) ?? []
        let files = names
            .filter { !$0.hasPrefix(".") && Store.videoExts.contains(($0 as NSString).pathExtension.lowercased()) }
            .sorted()
            .map { VideoFile(path: "\(videosDir)/\($0)", name: $0) }
        videoFiles = files
        loadFileDurations(files)
    }

    private func loadFileDurations(_ files: [VideoFile]) {
        for f in files where fileDurations[f.path] == nil {
            let asset = AVURLAsset(url: URL(fileURLWithPath: f.path))
            Task { [weak self] in
                guard let d = try? await asset.load(.duration) else { return }
                let secs = d.seconds
                guard secs.isFinite, secs > 0 else { return }
                await MainActor.run { self?.fileDurations[f.path] = secs }
            }
        }
    }

    /// Ranges (by index) whose resolved source path is this file — drives the file's timeline strip.
    func ranges(forFile path: String) -> [(index: Int, range: Range)] {
        let target = (path as NSString).standardizingPath
        var out: [(Int, Range)] = []
        for (i, r) in edl.ranges.enumerated() {
            if let sp = sourcePaths[r.source], (sp as NSString).standardizingPath == target {
                out.append((i, r))
            }
        }
        return out
    }

    // MARK: - Edits

    private func boundaries(for source: String) -> [Double] {
        guard let words = transcripts[source] else { return [] }
        return VirtualTime.sourceBoundaries(words)
    }

    /// Commit a new EDL: snapshot for undo, recompute total, atomic-write, append log, rebuild.
    private func commit(_ newEdl: Edl, op: String, fields: [String: Any]) {
        undoStack.append(edl)
        redoStack.removeAll()
        edl = newEdl.withRecomputedTotal()
        persist()
        if let dir { Project.appendLog(dir: dir, op: op, fields: fields) }
        rebuild(preservePlayhead: true)
    }

    private func persist() {
        guard let path = edlPath else { return }
        let data = edl.encoded()
        lastWrittenJSON = data
        Project.save(edl, to: path)
    }

    func setIn(_ index: Int, to value: Double, snapping: Bool = true) {
        guard index >= 0, index < edl.ranges.count else { return }
        var r = edl.ranges[index]
        var v = value
        if snapping { v = VirtualTime.snap(boundaries(for: r.source), v) }
        v = max(0, min(v, r.end - 0.2))
        guard abs(v - r.start) > 1e-4 else { return }
        let before = r.start
        r.start = v
        var e = edl; e.ranges[index] = r
        commit(e, op: "trim", fields: ["segment": index, "field": "start", "before": before, "after": v])
    }

    func setOut(_ index: Int, to value: Double, snapping: Bool = true) {
        guard index >= 0, index < edl.ranges.count else { return }
        var r = edl.ranges[index]
        var v = value
        if snapping { v = VirtualTime.snap(boundaries(for: r.source), v) }
        let maxEnd = sourceDurations[r.source] ?? Double.greatestFiniteMagnitude
        v = min(max(v, r.start + 0.2), maxEnd)
        guard abs(v - r.end) > 1e-4 else { return }
        let before = r.end
        r.end = v
        var e = edl; e.ranges[index] = r
        commit(e, op: "trim", fields: ["segment": index, "field": "end", "before": before, "after": v])
    }

    func nudgeIn(_ index: Int, by delta: Double) {
        guard index >= 0, index < edl.ranges.count else { return }
        setIn(index, to: edl.ranges[index].start + delta, snapping: false)
    }

    func nudgeOut(_ index: Int, by delta: Double) {
        guard index >= 0, index < edl.ranges.count else { return }
        setOut(index, to: edl.ranges[index].end + delta, snapping: false)
    }

    func deleteSlice(_ index: Int) {
        guard index >= 0, index < edl.ranges.count else { return }
        let removed = edl.ranges[index]
        var e = edl; e.ranges.remove(at: index)
        selection = nil
        commit(e, op: "delete", fields: ["segment": index, "range": [
            "source": removed.source, "start": removed.start, "end": removed.end,
            "beat": removed.beat ?? ""]])
    }

    func undo() {
        guard let prev = undoStack.popLast() else { return }
        redoStack.append(edl)
        edl = prev
        selection = nil
        persist()
        rebuild(preservePlayhead: true)
    }

    func redo() {
        guard let next = redoStack.popLast() else { return }
        undoStack.append(edl)
        edl = next
        selection = nil
        persist()
        rebuild(preservePlayhead: true)
    }

    var canUndo: Bool { !undoStack.isEmpty }
    var canRedo: Bool { !redoStack.isEmpty }

    // MARK: - Export

    func export(preview: Bool) {
        guard let path = edlPath, let dir, !exportRunning else { return }
        let out = "\(dir)/\(preview ? "preview.mp4" : "final.mp4")"
        exportRunning = true
        exportOutput = "$ video-use render \(path) -o \(out)\(preview ? " --preview" : "")\n"
        showExport = true

        let task = Process()
        task.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        var args = ["video-use", "render", path, "-o", out]
        if preview { args.append("--preview") }
        task.arguments = args
        var env = ProcessInfo.processInfo.environment
        let home = env["HOME"] ?? NSHomeDirectory()
        let extra = "\(home)/.local/bin:/opt/homebrew/bin:/usr/local/bin"
        env["PATH"] = (env["PATH"].map { "\($0):\(extra)" }) ?? extra
        task.environment = env

        let pipe = Pipe()
        task.standardOutput = pipe
        task.standardError = pipe
        pipe.fileHandleForReading.readabilityHandler = { [weak self] h in
            let d = h.availableData
            guard !d.isEmpty, let s = String(data: d, encoding: .utf8) else { return }
            DispatchQueue.main.async { self?.exportOutput += s }
        }
        task.terminationHandler = { [weak self] proc in
            pipe.fileHandleForReading.readabilityHandler = nil
            DispatchQueue.main.async {
                self?.exportOutput += "\n[exit \(proc.terminationStatus)]\n"
                self?.exportRunning = false
            }
        }
        do { try task.run() } catch {
            exportOutput += "\nFailed to launch video-use: \(error.localizedDescription)\nIs it on PATH?\n"
            exportRunning = false
        }
    }

    // MARK: - Remote control (ControlServer)

    func stateJSON() -> String {
        var slices: [[String: Any]] = []
        for (i, r) in edl.ranges.enumerated() {
            slices.append([
                "index": i,
                "beat": r.beat ?? "",
                "source": r.source,
                "start": r.start,
                "end": r.end,
                "out_start": offsets[i],
                "out_end": offsets[i + 1],
                "duration": r.duration,
                "quote": r.quote ?? "",
                "reason": r.reason ?? "",
            ])
        }
        let obj: [String: Any] = [
            "edlPath": edlPath ?? "",
            "slices": slices,
            "selection": selection ?? NSNull(),
            "playing": playing,
            "playhead": (playhead * 1000).rounded() / 1000,
        ]
        guard let data = try? JSONSerialization.data(withJSONObject: obj, options: [.withoutEscapingSlashes]) else { return "{}" }
        return String(data: data, encoding: .utf8) ?? "{}"
    }

    func handleRemote(_ body: String) {
        guard let data = body.data(using: .utf8),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let op = obj["op"] as? String else { return }
        switch op {
        case "open":     if let p = obj["path"] as? String { open(path: p) }
        case "toggle":   togglePlay()
        case "play":     play()
        case "pause":    pause()
        case "seek":     if let t = (obj["t"] as? NSNumber)?.doubleValue { seek(to: t) }
        case "select":   select(obj["i"] as? Int)
        case "undo":     undo()
        case "redo":     redo()
        case "reload":   reload()
        case "export":   export(preview: (obj["preview"] as? Bool) ?? false)
        default:         break
        }
    }
}
