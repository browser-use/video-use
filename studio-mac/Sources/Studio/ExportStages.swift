import Foundation

// Maps a line of `video-use render` stdout to a human-readable pipeline stage for the export
// sheet. Pure and side-effect free so it can be unit-tested against the real render.py output
// strings (extracting N segment(s) → / concat → / master SRT / compositing → / loudnorm).
enum ExportStages {
    static func label(for rawLine: String) -> String? {
        let line = rawLine.lowercased()
        if line.contains("extracting") && line.contains("segment") { return "Extracting segments" }
        if line.contains("concat") { return "Concatenating clips" }
        if line.contains("master srt") { return "Building subtitles" }
        if line.contains("compositing") { return "Compositing overlays & subtitles" }
        if line.contains("loudnorm") || line.contains("loudness normalization") { return "Normalizing loudness" }
        return nil
    }

    static func isErrorLine(_ rawLine: String) -> Bool {
        let line = rawLine.lowercased()
        return line.contains("error") || line.contains("traceback") || line.contains("exception")
            || line.contains("no such") || line.hasPrefix("fatal")
    }
}
