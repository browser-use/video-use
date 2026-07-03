import AVFoundation
import SwiftUI

// The "stage": the crisp 16:9 hero floating on an ambient bloom of the footage's own colors
// (a blurred, dimmed resize-fill copy of the same player). Screen Studio's signature move —
// the app feels lit by the video instead of sitting a thumbnail on flat black.

/// Full-bleed blurred backdrop driven by the same AVPlayer as the hero.
struct AmbilightBackdrop: NSViewRepresentable {
    let player: AVPlayer

    func makeNSView(context: Context) -> AmbilightNSView {
        let v = AmbilightNSView()
        v.attach(player)
        return v
    }
    func updateNSView(_ v: AmbilightNSView, context: Context) { v.attach(player) }
}

final class AmbilightNSView: NSView {
    private let playerLayer = AVPlayerLayer()

    override init(frame frameRect: NSRect) {
        super.init(frame: frameRect)
        wantsLayer = true
        let root = CALayer()
        root.backgroundColor = CGColor(red: 0.04, green: 0.04, blue: 0.05, alpha: 1)
        layer = root
        playerLayer.videoGravity = .resizeAspectFill
        // Punch the saturation so the bloom reads as the footage's colors, then blur wide.
        var filters: [CIFilter] = []
        if let sat = CIFilter(name: "CIColorControls",
                              parameters: [kCIInputSaturationKey: 1.7, kCIInputBrightnessKey: -0.04]) {
            filters.append(sat)
        }
        if let blur = CIFilter(name: "CIGaussianBlur", parameters: [kCIInputRadiusKey: 64]) {
            filters.append(blur)
        }
        playerLayer.filters = filters
        root.addSublayer(playerLayer)
    }
    required init?(coder: NSCoder) { fatalError("init(coder:) not used") }

    func attach(_ p: AVPlayer) { if playerLayer.player !== p { playerLayer.player = p } }

    override func layout() {
        super.layout()
        // Oversize so the gaussian blur's clamped, transparent edges never show.
        playerLayer.frame = bounds.insetBy(dx: -110, dy: -110)
    }
}

struct StageView<Overlay: View>: View {
    let player: AVPlayer
    let aspect: Double
    var zoomScale: CGFloat = 1
    var zoomAnchor: UnitPoint = .center
    @ViewBuilder var overlay: () -> Overlay

    var body: some View {
        ZStack {
            AmbilightBackdrop(player: player)
            // Dim + vignette so the crisp hero pops off the bloom.
            Rectangle().fill(Color.black.opacity(0.5))
            RadialGradient(
                colors: [.clear, Color.black.opacity(0.62)],
                center: .center, startRadius: 160, endRadius: 780)
            LinearGradient(
                colors: [.black.opacity(0.35), .clear, .black.opacity(0.28)],
                startPoint: .top, endPoint: .bottom)

            PlayerView(player: player)
                .aspectRatio(aspect, contentMode: .fit)
                .scaleEffect(zoomScale, anchor: zoomAnchor)   // virtual camera push-in
                .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
                .overlay { overlay() }
                .overlay(
                    RoundedRectangle(cornerRadius: 16, style: .continuous)
                        .stroke(Color.white.opacity(0.07), lineWidth: 1))
                .shadow(color: .black.opacity(0.6), radius: 48, y: 22)
                .padding(18)
        }
        .clipped()
    }
}
