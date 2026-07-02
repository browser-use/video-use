import AVFoundation
import SwiftUI

// AVPlayerLayer host. The Store owns one AVPlayer for its whole lifetime and swaps items
// on rebuild, so this layer just tracks that single player.
struct PlayerView: NSViewRepresentable {
    let player: AVPlayer

    func makeNSView(context: Context) -> PlayerHostView {
        let v = PlayerHostView()
        v.playerLayer.player = player
        return v
    }

    func updateNSView(_ nsView: PlayerHostView, context: Context) {
        if nsView.playerLayer.player !== player { nsView.playerLayer.player = player }
    }
}

final class PlayerHostView: NSView {
    let playerLayer = AVPlayerLayer()

    override init(frame frameRect: NSRect) {
        super.init(frame: frameRect)
        wantsLayer = true
        let root = CALayer()
        root.backgroundColor = CGColor(red: 0, green: 0, blue: 0, alpha: 1)
        root.cornerRadius = 12
        root.masksToBounds = true
        layer = root
        playerLayer.videoGravity = .resizeAspect
        playerLayer.backgroundColor = CGColor(red: 0, green: 0, blue: 0, alpha: 1)
        root.addSublayer(playerLayer)
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) not used") }

    override func layout() {
        super.layout()
        playerLayer.frame = bounds
    }
}
