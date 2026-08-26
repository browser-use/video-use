import SwiftUI

// Screen Studio-style virtual camera: during a zoom region the preview eases into a focus point
// and holds, then eases back out. Pure function of the playhead so it's correct while playing and
// while scrubbing. This drives the live preview; the same regions are what an export would burn in.
struct ZoomRegion: Equatable {
    var start: Double        // output time
    var end: Double
    var scale: Double        // e.g. 1.6 = 160%
    var focus: UnitPoint     // where in the frame to push toward
    var focusEnd: UnitPoint? = nil // Auto-drift target: camera slowly pans focus -> focusEnd across the hold
    var ramp: Double = 0.6   // ease in/out seconds
}

enum ZoomCamera {
    /// Resolved camera at output time t: the scale (envelope) and the anchor (with a slow drift).
    static func resolve(at t: Double, regions: [ZoomRegion]) -> (scale: CGFloat, anchor: UnitPoint) {
        for r in regions where t >= r.start && t <= r.end {
            let ramp = max(0.0001, r.ramp)
            let rising = easeOutCubic(min(1, (t - r.start) / ramp))
            let falling = easeOutCubic(min(1, (r.end - t) / ramp))
            let k = min(rising, falling)                 // 0 at edges, 1 in the hold
            let scale = 1 + (r.scale - 1) * k

            // Auto-pan: ease the focus across the whole region so the camera feels alive/following.
            let span = max(0.0001, r.end - r.start)
            let p = easeInOutCubic((t - r.start) / span)
            let target = r.focusEnd ?? r.focus
            let anchor = UnitPoint(x: lerp(Double(r.focus.x), Double(target.x), p),
                                   y: lerp(Double(r.focus.y), Double(target.y), p))
            return (CGFloat(scale), anchor)
        }
        return (1, .center)
    }

    static func easeOutCubic(_ t: Double) -> Double {
        let c = min(1, max(0, t)); return 1 - pow(1 - c, 3)
    }
    static func easeInOutCubic(_ t: Double) -> Double {
        let c = min(1, max(0, t))
        return c < 0.5 ? 4 * c * c * c : 1 - pow(-2 * c + 2, 3) / 2
    }
    static func lerp(_ a: Double, _ b: Double, _ t: Double) -> Double { a + (b - a) * t }
}
