import SwiftUI

// Live caption generation from per-source transcripts + the current ranges — a faithful port
// of render.py build_master_srt so the on-screen captions match the exported burn-in and stay
// correct as slices get trimmed. We never read master.srt (it goes stale the moment a cut changes).

struct Cue: Equatable {
    var start: Double     // output time
    var end: Double
    var text: String
}

enum SubtitleEngine {
    private static let punctBreak: Set<Character> = [".", ",", "!", "?", ";", ":"]

    static func build(ranges: [Range], transcripts: [String: [Word]], style: SubtitleStyle) -> [Cue] {
        var cues: [Cue] = []
        var segOffset = 0.0
        let chunkN = max(1, style.chunk_words)

        for r in ranges {
            let segStart = r.start
            let segEnd = r.end
            let segDur = max(0, segEnd - segStart)
            guard let words = transcripts[r.source] else { segOffset += segDur; continue }

            // Words overlapping [segStart, segEnd) — mirrors render.py _words_in_range.
            let inSeg = words.filter { w in
                (w.type ?? "word") == "word" && w.end > segStart && w.start < segEnd
            }

            // Group into N-word chunks, breaking early on trailing punctuation.
            var chunks: [[Word]] = []
            var current: [Word] = []
            for w in inSeg {
                let text = w.text.trimmingCharacters(in: .whitespaces)
                if text.isEmpty { continue }
                current.append(w)
                let endsPunct = text.last.map { punctBreak.contains($0) } ?? false
                if current.count >= chunkN || endsPunct {
                    chunks.append(current)
                    current = []
                }
            }
            if !current.isEmpty { chunks.append(current) }

            for chunk in chunks {
                let localStart = max(segStart, chunk.first!.start)
                let localEnd = min(segEnd, chunk.last!.end)
                let outStart = max(0, localStart - segStart) + segOffset
                var outEnd = max(0, localEnd - segStart) + segOffset
                if outEnd <= outStart { outEnd = outStart + 0.4 }

                var text = chunk
                    .map { $0.text.trimmingCharacters(in: .whitespaces) }
                    .joined(separator: " ")
                text = text.replacingOccurrences(of: "\\s+", with: " ", options: .regularExpression)
                    .trimmingCharacters(in: .whitespaces)
                // Scribe puts literal quote marks in reported-speech words ("Hey. / cash."). Strip
                // straight + curly double quotes (keep apostrophes) to match render.py build_master_srt.
                for q in ["\"", "\u{201C}", "\u{201D}"] { text = text.replacingOccurrences(of: q, with: "") }
                // render.py strips only trailing , ; : (keeps . ! ?)
                while let last = text.last, ",;:".contains(last) { text.removeLast() }
                if style.uppercase { text = text.uppercased() }

                cues.append(Cue(start: outStart, end: outEnd, text: text))
            }

            segOffset += segDur
        }

        cues.sort { $0.start < $1.start }
        return cues
    }

    static func active(_ cues: [Cue], at t: Double) -> String? {
        for c in cues where t >= c.start && t < c.end { return c.text }
        return nil
    }
}

/// Caption drawn over the preview: centered, white bold with a black outline, positioned by
/// margin_v. Sizes are expressed in the export's 1080p space and scaled to the on-screen video rect.
struct SubtitleOverlay: View {
    let text: String
    let style: SubtitleStyle

    var body: some View {
        GeometryReader { geo in
            let h = geo.size.height
            let fontSize = max(8, style.size / 1080 * h)
            let margin = style.margin_v / 1080 * h
            VStack {
                Spacer()
                CaptionLabel(text: text, fontSize: fontSize)
                    .padding(.horizontal, 12)
                    .padding(.bottom, margin)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .allowsHitTesting(false)
    }
}

private struct CaptionLabel: View {
    let text: String
    let fontSize: CGFloat

    var body: some View {
        let font = Font.system(size: fontSize, weight: .bold)
        return ZStack {
            // Black outline via 8 offset copies.
            ForEach(Array(outlineOffsets.enumerated()), id: \.offset) { _, off in
                Text(text).font(font).foregroundColor(.black)
                    .offset(x: off.width, y: off.height)
            }
            Text(text).font(font).foregroundColor(.white)
        }
        .multilineTextAlignment(.center)
        .shadow(color: .black.opacity(0.6), radius: 2, y: 1)
    }

    private var outlineOffsets: [CGSize] {
        let d = max(1, fontSize * 0.09)
        return [
            CGSize(width: -d, height: 0), CGSize(width: d, height: 0),
            CGSize(width: 0, height: -d), CGSize(width: 0, height: d),
            CGSize(width: -d, height: -d), CGSize(width: d, height: -d),
            CGSize(width: -d, height: d), CGSize(width: d, height: d),
        ]
    }
}
