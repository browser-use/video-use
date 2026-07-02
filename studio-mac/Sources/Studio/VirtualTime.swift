import Foundation

// Pure output<->source time math for the virtual cut. Ported from studio/src/lib/virtualTime.ts.
// The AVMutableComposition makes output time == composition time, so these are used for the
// timeline layout, the inspector, and mapping a selection to a seek target.
enum VirtualTime {
    /// offsets[i] = output time at which segment i begins; offsets[n] = total duration.
    static func segmentOffsets(_ ranges: [Range]) -> [Double] {
        var offsets: [Double] = [0]
        for r in ranges { offsets.append(offsets[offsets.count - 1] + r.duration) }
        return offsets
    }

    static func totalDuration(_ ranges: [Range]) -> Double {
        ranges.reduce(0.0) { $0 + $1.duration }
    }

    /// Index of the segment containing output time t (last segment when t >= total). nil when empty.
    static func segmentAtOutput(_ offsets: [Double], _ t: Double) -> Int? {
        let n = offsets.count - 1
        if n <= 0 { return nil }
        if t <= 0 { return 0 }
        if t >= offsets[n] { return n - 1 }
        var lo = 0, hi = n - 1
        while lo < hi {
            let mid = (lo + hi + 1) / 2
            if offsets[mid] <= t { lo = mid } else { hi = mid - 1 }
        }
        return lo
    }

    /// Sorted, deduped snap targets: every word start and end (type "word" only).
    static func sourceBoundaries(_ words: [Word]) -> [Double] {
        var set = Set<Double>()
        for w in words where (w.type ?? "word") == "word" {
            set.insert(w.start)
            set.insert(w.end)
        }
        return set.sorted()
    }

    /// Nearest boundary to t (binary search); returns t unchanged when there are none.
    static func snap(_ bounds: [Double], _ t: Double) -> Double {
        if bounds.isEmpty { return t }
        var lo = 0, hi = bounds.count - 1
        while lo < hi {
            let mid = (lo + hi) / 2
            if bounds[mid] < t { lo = mid + 1 } else { hi = mid }
        }
        if lo > 0 && abs(bounds[lo - 1] - t) <= abs(bounds[lo] - t) { return bounds[lo - 1] }
        return bounds[lo]
    }

    /// "00:12.4" — minutes:seconds.tenths, output-timeline display format.
    static func fmt(_ s: Double) -> String {
        let c = max(0, s)
        let m = Int(c / 60)
        let sec = c - Double(m) * 60
        let whole = Int(sec)
        let tenth = Int((sec - Double(whole)) * 10)
        return String(format: "%02d:%02d.%d", m, whole, tenth)
    }
}
