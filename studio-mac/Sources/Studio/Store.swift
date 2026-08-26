import AppKit
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

    // Zoom camera (Screen Studio-style push-ins). Prototype: seeded with a demo region on load.
    @Published var zoomRegions: [ZoomRegion] = []
    @Published var selectedZoom: Int?

    // Files pane
    @Published var showFiles = false
    @Published var videoFiles: [VideoFile] = []
    @Published var fileDurations: [String: Double] = [:]

    // Export sheet
    @Published var exportRunning = false
    @Published var exportOutput = ""          // full raw log (behind "Show log")
    @Published var showExport = false
    @Published var exportStage = ""           // current pipeline stage, human-readable
    @Published var exportSucceeded: Bool?     // nil while running
    @Published var exportOutPath: String?
    @Published var exportSizeMB: Double?
    @Published var exportDuration: Double?
    @Published var exportError: String?
    private var exportProcess: Process?

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
        let interval = CMTime(value: 1, timescale: 60)   // 60fps so the zoom camera is buttery
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
        // Track when the window becomes key so we can swallow a control click that lands
        // purely from window activation (see updateSubtitleStyle).
        NotificationCenter.default.addObserver(
            forName: NSApplication.didBecomeActiveNotification, object: nil, queue: .main
        ) { [weak self] _ in self?.lastBecameActive = Date() }
    }

    private var lastBecameActive = Date.distantPast

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

    /// Loads the project OFF the main thread — reading edl.json + transcripts on the main thread
    /// froze the whole UI whenever the external/exFAT drive was busy (e.g. mid 4K render).
    func open(path: String) {
        Task { [weak self] in
            let loaded: LoadedProject
            do { loaded = try Project.load(path) }
            catch {
                await MainActor.run { self?.loadError = "Could not open \(path): \(error.localizedDescription)" }
                return
            }
            await MainActor.run {
                guard let self else { return }
                self.edlPath = loaded.edlPath
                self.dir = loaded.dir
                self.edl = loaded.edl
                self.sourcePaths = loaded.sourcePaths
                self.transcripts = loaded.transcripts
                self.projectMd = loaded.projectMd
                self.loadError = nil
                self.undoStack.removeAll(); self.redoStack.removeAll()
                self.selection = nil
                self.lastWrittenJSON = nil
                self.subtitleStyle = loaded.edl.subtitle_style ?? .default
                self.zoomRegions = []
                self.rebuild(preservePlayhead: false)
                self.discoverFiles()
                self.watcher?.stop()
                self.watcher = FileWatcher(path: path) { [weak self] in
                    DispatchQueue.main.async { self?.externalChange() }
                }
            }
        }
    }

    func reload() {
        guard let path = edlPath else { return }
        open(path: path)
    }

    private func externalChange() {
        guard let path = edlPath else { return }
        Task { [weak self] in
            guard let data = try? Data(contentsOf: URL(fileURLWithPath: path)),
                  let fresh = try? JSONDecoder().decode(Edl.self, from: data) else { return }
            await MainActor.run {
                guard let self else { return }
                if let last = self.lastWrittenJSON, last == data { return }   // our own write
                if fresh == self.edl { return }
                self.edl = fresh
                self.subtitleStyle = fresh.subtitle_style ?? .default
                self.rebuild(preservePlayhead: true)
                self.flashSynced()
            }
        }
    }

    private func flashSynced() {
        withAnimation(.easeOut(duration: 0.2)) { agentSynced = true }
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.6) { [weak self] in
            withAnimation(.easeIn(duration: 0.4)) { self?.agentSynced = false }
        }
    }

    // MARK: - Composition

    private var rebuildGen = 0

    /// Build the AVMutableComposition off the main actor (track loading is async so 4K sources
    /// off a slow drive can't be dropped), then swap the player item on main. A generation guard
    /// drops superseded rebuilds if several fire in quick succession (e.g. watcher bursts).
    private func rebuild(preservePlayhead: Bool) {
        let keep = playhead
        let edlSnapshot = edl
        let paths = sourcePaths
        let wasPlaying = playing
        rebuildGen &+= 1
        let gen = rebuildGen
        Task { [weak self] in
            let result = await CompositionBuilder.build(edl: edlSnapshot, sourcePaths: paths)
            await MainActor.run {
                guard let self, gen == self.rebuildGen else { return }
                self.offsets = result.offsets
                self.total = result.total
                self.sourceDurations = result.sourceDurations
                if result.renderSize.width > 0, result.renderSize.height > 0 {
                    self.renderAspect = result.renderSize.width / result.renderSize.height
                }
                self.refreshCues()

                let item = AVPlayerItem(asset: result.composition)
                item.videoComposition = result.videoComposition
                self.player.replaceCurrentItem(with: item)
                if preservePlayhead {
                    self.seek(to: min(keep, self.total))
                } else {
                    self.playhead = 0
                }
                if wasPlaying && preservePlayhead { self.player.play() }
            }
        }
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

    private var scrubbing = false
    private var scrubTarget: Double?

    /// select:false skips updating the slice selection (used for hover/drag scrubbing so the
    /// inspector doesn't thrash). precise:false coalesces seeks — the playhead updates instantly
    /// while only one tolerant 4K seek is ever in flight — so scrubbing stays responsive.
    func seek(to t: Double, select: Bool = true, precise: Bool = true) {
        let clamped = max(0, min(t, total))
        playhead = clamped
        if select { selection = VirtualTime.segmentAtOutput(offsets, clamped) }
        if precise {
            let time = CMTime(seconds: clamped, preferredTimescale: 600)
            player.seek(to: time, toleranceBefore: .zero, toleranceAfter: .zero)
        } else {
            scrubTarget = clamped
            pumpScrub()
        }
    }

    private func pumpScrub() {
        guard !scrubbing, let t = scrubTarget else { return }
        scrubTarget = nil
        scrubbing = true
        let time = CMTime(seconds: t, preferredTimescale: 600)
        let tol = CMTime(seconds: 0.25, preferredTimescale: 600)
        player.seek(to: time, toleranceBefore: tol, toleranceAfter: tol) { [weak self] _ in
            self?.scrubbing = false
            self?.pumpScrub()
        }
    }

    /// Seek to the output-time start of a segment.
    func seekToSegment(_ i: Int) {
        guard i >= 0, i < offsets.count - 1 else { return }
        seek(to: offsets[i])
    }

    func select(_ i: Int?) {
        if let i, i >= 0, i < edl.ranges.count { selection = i } else { selection = nil }
        if i != nil { selectedZoom = nil }
    }

    // MARK: - Zoom regions (Screen Studio-style camera)

    func selectZoom(_ i: Int?) {
        guard let i, i >= 0, i < zoomRegions.count else { selectedZoom = nil; return }
        let r = zoomRegions[i]
        seek(to: (r.start + r.end) / 2)      // jump into the region so the push-in is visible
        selectedZoom = i                     // after seek (seek sets slice selection, not zoom)
    }

    func addZoomAtPlayhead() {
        let start = min(playhead, max(0, total - 2))
        let end = min(start + 2.0, total)
        zoomRegions.append(ZoomRegion(start: start, end: end, scale: 1.5,
                                      focus: UnitPoint(x: 0.5, y: 0.45), ramp: 0.6))
        zoomRegions.sort { $0.start < $1.start }
        selectZoom(zoomRegions.firstIndex { $0.start <= playhead && playhead <= $0.end })
    }

    func removeZoom(_ i: Int) {
        guard i >= 0, i < zoomRegions.count else { return }
        zoomRegions.remove(at: i)
        selectedZoom = nil
    }

    // MARK: - Subtitles

    func refreshCues() {
        cues = SubtitleEngine.build(ranges: edl.ranges, transcripts: transcripts, style: subtitleStyle)
    }

    var currentCaption: String? {
        guard subtitleStyle.enabled else { return nil }
        return SubtitleEngine.active(cues, at: playhead)
    }

    /// Update the live subtitle style. `commit` persists to edl.json + edit_log and is undoable;
    /// slider drags pass commit:false while dragging and commit:true on release.
    func updateSubtitleStyle(_ new: SubtitleStyle, commit: Bool) {
        subtitleStyle = new
        refreshCues()
        guard commit else { return }

        // Swallow a commit that lands within the window-activation grace window: the first click
        // on an inactive window (delivered to a control purely to focus it) shouldn't nudge +
        // persist the style. Revert the live preview too.
        if Date().timeIntervalSince(lastBecameActive) < 0.35 {
            subtitleStyle = edl.subtitle_style ?? .default
            refreshCues()
            return
        }

        undoStack.append(edl)          // undoable: snapshot the pre-change EDL
        redoStack.removeAll()
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
        commit(e, op: "trim", fields: ["segment": index, "beat": r.beat ?? "", "source": r.source,
                                       "field": "start", "before": before, "after": v])
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
        commit(e, op: "trim", fields: ["segment": index, "beat": r.beat ?? "", "source": r.source,
                                       "field": "end", "before": before, "after": v])
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
        subtitleStyle = prev.subtitle_style ?? .default
        persist()
        rebuild(preservePlayhead: true)
    }

    func redo() {
        guard let next = redoStack.popLast() else { return }
        undoStack.append(edl)
        edl = next
        selection = nil
        subtitleStyle = next.subtitle_style ?? .default
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
        exportSucceeded = nil
        exportOutput = ""
        exportStage = "Starting…"
        exportError = nil
        exportSizeMB = nil
        exportDuration = nil
        exportOutPath = out
        showExport = true

        let task = Process()
        task.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        // Always rebuild subtitles so trimmed cuts don't reuse a stale master.srt.
        // render honors subtitle_style (enabled=false → no subtitles).
        var args = ["video-use", "render", path, "-o", out, "--build-subtitles"]
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
            DispatchQueue.main.async { self?.ingestExportOutput(s) }
        }
        task.terminationHandler = { [weak self] proc in
            pipe.fileHandleForReading.readabilityHandler = nil
            DispatchQueue.main.async { self?.finishExport(status: proc.terminationStatus, out: out) }
        }
        exportProcess = task
        do { try task.run() } catch {
            exportRunning = false
            exportSucceeded = false
            exportStage = "Failed"
            exportError = "Could not launch video-use — is it on your PATH?"
            exportOutput += "\(error.localizedDescription)\n"
        }
    }

    func cancelExport() {
        exportProcess?.terminate()
    }

    private func ingestExportOutput(_ chunk: String) {
        exportOutput += chunk
        for raw in chunk.split(whereSeparator: \.isNewline) {
            let line = String(raw)
            if let stage = ExportStages.label(for: line) { exportStage = stage }
            if ExportStages.isErrorLine(line) { exportError = line.trimmingCharacters(in: .whitespaces) }
        }
    }

    private func finishExport(status: Int32, out: String) {
        exportRunning = false
        exportProcess = nil
        if status == 0 {
            exportSucceeded = true
            exportStage = "Done"
            if let attrs = try? FileManager.default.attributesOfItem(atPath: out),
               let n = attrs[.size] as? NSNumber {
                exportSizeMB = n.doubleValue / 1_000_000
            }
            let asset = AVURLAsset(url: URL(fileURLWithPath: out))
            Task { [weak self] in
                guard let d = try? await asset.load(.duration) else { return }
                await MainActor.run { self?.exportDuration = d.seconds }
            }
        } else {
            exportSucceeded = false
            exportStage = "Failed"
            if exportError == nil { exportError = "Export failed (exit \(status))." }
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
        case "substyle":
            var s = subtitleStyle
            if let v = (obj["size"] as? NSNumber)?.doubleValue { s.size = v }
            if let v = (obj["margin_v"] as? NSNumber)?.doubleValue { s.margin_v = v }
            if let v = obj["uppercase"] as? Bool { s.uppercase = v }
            if let v = obj["chunk_words"] as? Int { s.chunk_words = v }
            if let v = obj["enabled"] as? Bool { s.enabled = v }
            updateSubtitleStyle(s, commit: (obj["commit"] as? Bool) ?? false)
        case "delete":   if let i = obj["i"] as? Int ?? selection { deleteSlice(i) }
        default:         break
        }
    }
}
