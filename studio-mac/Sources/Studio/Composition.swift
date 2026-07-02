import AVFoundation
import CoreGraphics
import CoreMedia

// The core win of the native app: turn the EDL's ranges into ONE gapless
// AVMutableComposition and hand it to a single AVPlayer. Each source segment is
// insertTimeRange'd back-to-back onto shared video/audio tracks — frame-accurate,
// hardware-decoded, gapless virtual-cut playback (including 4K), no per-source
// stacked players. A per-segment AVVideoComposition applies each source's
// orientation/scale so mixed-resolution / portrait sources render correctly.

struct CompositionResult {
    let composition: AVMutableComposition
    let videoComposition: AVMutableVideoComposition?
    let offsets: [Double]            // prefix sums, count = ranges.count + 1
    let total: Double
    let sourceDurations: [String: Double]
}

enum CompositionBuilder {
    private static let ts: CMTimeScale = 600

    static func build(edl: Edl, sourcePaths: [String: String]) -> CompositionResult {
        let comp = AVMutableComposition()
        let vTrack = comp.addMutableTrack(withMediaType: .video, preferredTrackID: kCMPersistentTrackID_Invalid)
        let aTrack = comp.addMutableTrack(withMediaType: .audio, preferredTrackID: kCMPersistentTrackID_Invalid)

        var assets: [String: AVURLAsset] = [:]
        var sourceDurations: [String: Double] = [:]
        func asset(for source: String) -> AVURLAsset? {
            guard let path = sourcePaths[source] else { return nil }
            if let a = assets[path] { return a }
            let a = AVURLAsset(url: URL(fileURLWithPath: path))
            assets[path] = a
            sourceDurations[source] = a.duration.seconds
            return a
        }

        // Pass 1: pick a render size from the first oriented video track we can find.
        var renderSize: CGSize = .zero
        for r in edl.ranges {
            if let a = asset(for: r.source), let vt = a.tracks(withMediaType: .video).first {
                let o = vt.naturalSize.applying(vt.preferredTransform)
                renderSize = CGSize(width: abs(o.width), height: abs(o.height))
                break
            }
        }

        // Pass 2: insert each segment back-to-back and build one video instruction per segment.
        var offsets: [Double] = [0]
        var cursor = CMTime.zero
        var instructions: [AVMutableVideoCompositionInstruction] = []

        for r in edl.ranges {
            let dur = r.duration
            offsets.append(offsets[offsets.count - 1] + dur)
            if dur <= 0 { continue }

            let segDur = CMTime(seconds: dur, preferredTimescale: ts)
            let srcRange = CMTimeRange(start: CMTime(seconds: r.start, preferredTimescale: ts), duration: segDur)
            let segRange = CMTimeRange(start: cursor, duration: segDur)

            let a = asset(for: r.source)
            let vSrc = a?.tracks(withMediaType: .video).first
            let aSrc = a?.tracks(withMediaType: .audio).first

            // Video — keep the composition track length aligned even if a segment can't load.
            if let vSrc, let vTrack, (try? vTrack.insertTimeRange(srcRange, of: vSrc, at: cursor)) != nil {
                if !renderSize.equalTo(.zero) {
                    let li = AVMutableVideoCompositionLayerInstruction(assetTrack: vTrack)
                    li.setTransform(fitTransform(track: vSrc, into: renderSize), at: cursor)
                    let inst = AVMutableVideoCompositionInstruction()
                    inst.timeRange = segRange
                    inst.backgroundColor = CGColor(red: 0, green: 0, blue: 0, alpha: 1)
                    inst.layerInstructions = [li]
                    instructions.append(inst)
                }
            } else {
                vTrack?.insertEmptyTimeRange(segRange)
            }

            // Audio
            if let aSrc, let aTrack, (try? aTrack.insertTimeRange(srcRange, of: aSrc, at: cursor)) != nil {
                // inserted
            } else {
                aTrack?.insertEmptyTimeRange(segRange)
            }

            cursor = cursor + segDur
        }

        var videoComposition: AVMutableVideoComposition?
        if !renderSize.equalTo(.zero) && !instructions.isEmpty {
            let vc = AVMutableVideoComposition()
            vc.renderSize = renderSize
            vc.frameDuration = CMTime(value: 1, timescale: 30)
            vc.instructions = instructions
            videoComposition = vc
        }

        return CompositionResult(
            composition: comp,
            videoComposition: videoComposition,
            offsets: offsets,
            total: offsets.last ?? 0,
            sourceDurations: sourceDurations
        )
    }

    /// Orientation + aspect-fit transform mapping a source frame into renderSize, centered.
    /// preferredTransform is applied first (so rotated sources land upright), then uniform
    /// scale to fit, then a translate to center any letterbox/pillarbox gap.
    private static func fitTransform(track: AVAssetTrack, into renderSize: CGSize) -> CGAffineTransform {
        let pref = track.preferredTransform
        let oriented = track.naturalSize.applying(pref)
        let ow = abs(oriented.width), oh = abs(oriented.height)
        guard ow > 0, oh > 0 else { return pref }
        let scale = min(renderSize.width / ow, renderSize.height / oh)
        let scaled = CGAffineTransform(scaleX: scale, y: scale)
        let tx = (renderSize.width - ow * scale) / 2
        let ty = (renderSize.height - oh * scale) / 2
        let centered = CGAffineTransform(translationX: tx, y: ty)
        return pref.concatenating(scaled).concatenating(centered)
    }
}
