import AVFoundation
import CoreGraphics
import CoreMedia

// The core win of the native app: turn the EDL's ranges into ONE gapless
// AVMutableComposition and hand it to a single AVPlayer. Each source segment is
// insertTimeRange'd back-to-back onto shared video/audio tracks — frame-accurate,
// hardware-decoded, gapless virtual-cut playback (including 4K), no per-source
// stacked players. A per-segment AVVideoComposition applies each source's
// orientation/scale so mixed-resolution / portrait sources render correctly.
//
// Tracks are loaded ASYNC: the deprecated synchronous asset.tracks() returns [] for
// 4K assets that haven't finished loading off a slow/external drive, which silently
// dropped segments and left gaps in the video-composition instructions (AVFoundation
// needs them to tile the whole timeline) — that froze playback on multi-source cuts.

struct CompositionResult {
    let composition: AVMutableComposition
    let videoComposition: AVMutableVideoComposition?
    let offsets: [Double]            // prefix sums, count = ranges.count + 1
    let total: Double
    let sourceDurations: [String: Double]
    let renderSize: CGSize
}

enum CompositionBuilder {
    private static let ts: CMTimeScale = 600

    private struct Loaded {
        let video: AVAssetTrack?
        let audio: AVAssetTrack?
        let naturalSize: CGSize
        let transform: CGAffineTransform
        let duration: Double
    }

    static func build(edl: Edl, sourcePaths: [String: String]) async -> CompositionResult {
        let comp = AVMutableComposition()
        let vTrack = comp.addMutableTrack(withMediaType: .video, preferredTrackID: kCMPersistentTrackID_Invalid)
        let aTrack = comp.addMutableTrack(withMediaType: .audio, preferredTrackID: kCMPersistentTrackID_Invalid)

        // Preload every unique source's tracks + geometry, reliably, before inserting anything.
        var loaded: [String: Loaded] = [:]
        for r in edl.ranges where loaded[r.source] == nil {
            guard let path = sourcePaths[r.source] else { continue }
            let asset = AVURLAsset(url: URL(fileURLWithPath: path))
            let dur = ((try? await asset.load(.duration))?.seconds) ?? 0
            let vTracks = (try? await asset.loadTracks(withMediaType: .video)) ?? []
            let aTracks = (try? await asset.loadTracks(withMediaType: .audio)) ?? []
            var natSize = CGSize.zero
            var transform = CGAffineTransform.identity
            if let vt = vTracks.first, let g = try? await vt.load(.naturalSize, .preferredTransform) {
                natSize = g.0
                transform = g.1
            }
            loaded[r.source] = Loaded(video: vTracks.first, audio: aTracks.first,
                                      naturalSize: natSize, transform: transform, duration: dur)
        }

        // Render size from the first source that actually has a video track.
        var renderSize = CGSize.zero
        for r in edl.ranges {
            if let l = loaded[r.source], l.video != nil, l.naturalSize != .zero {
                let o = l.naturalSize.applying(l.transform)
                renderSize = CGSize(width: abs(o.width), height: abs(o.height))
                break
            }
        }
        let hasVC = !renderSize.equalTo(.zero)

        var offsets: [Double] = [0]
        var cursor = CMTime.zero
        var instructions: [AVMutableVideoCompositionInstruction] = []
        var sourceDurations: [String: Double] = [:]
        for (src, l) in loaded { sourceDurations[src] = l.duration }

        for r in edl.ranges {
            let dur = r.duration
            offsets.append(offsets[offsets.count - 1] + dur)
            if dur <= 0 { continue }

            let segDur = CMTime(seconds: dur, preferredTimescale: ts)
            let srcRange = CMTimeRange(start: CMTime(seconds: r.start, preferredTimescale: ts), duration: segDur)
            let segRange = CMTimeRange(start: cursor, duration: segDur)
            let l = loaded[r.source]

            var placedVideo = false
            if let vSrc = l?.video, let vTrack,
               (try? vTrack.insertTimeRange(srcRange, of: vSrc, at: cursor)) != nil {
                placedVideo = true
                if hasVC {
                    let li = AVMutableVideoCompositionLayerInstruction(assetTrack: vTrack)
                    li.setTransform(fitTransform(naturalSize: l!.naturalSize, transform: l!.transform, into: renderSize), at: cursor)
                    let inst = AVMutableVideoCompositionInstruction()
                    inst.timeRange = segRange
                    inst.backgroundColor = CGColor(red: 0, green: 0, blue: 0, alpha: 1)
                    inst.layerInstructions = [li]
                    instructions.append(inst)
                }
            } else {
                vTrack?.insertEmptyTimeRange(segRange)
            }

            // Always tile the video composition: a segment with no video still needs a
            // (black) instruction covering its range, or the whole composition breaks.
            if hasVC && !placedVideo {
                let inst = AVMutableVideoCompositionInstruction()
                inst.timeRange = segRange
                inst.backgroundColor = CGColor(red: 0, green: 0, blue: 0, alpha: 1)
                inst.layerInstructions = []
                instructions.append(inst)
            }

            if let aSrc = l?.audio, let aTrack,
               (try? aTrack.insertTimeRange(srcRange, of: aSrc, at: cursor)) != nil {
                // inserted
            } else {
                aTrack?.insertEmptyTimeRange(segRange)
            }

            cursor = cursor + segDur
        }

        var videoComposition: AVMutableVideoComposition?
        if hasVC && !instructions.isEmpty {
            instructions.sort { $0.timeRange.start < $1.timeRange.start }
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
            sourceDurations: sourceDurations,
            renderSize: renderSize
        )
    }

    /// Orientation + aspect-fit transform mapping a source frame into renderSize, centered.
    private static func fitTransform(naturalSize: CGSize, transform pref: CGAffineTransform, into renderSize: CGSize) -> CGAffineTransform {
        let oriented = naturalSize.applying(pref)
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
