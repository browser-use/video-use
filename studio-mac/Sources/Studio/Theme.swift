import SwiftUI

// Design tokens from studio/DESIGN.md — Screen Studio reference: near-black chrome,
// one indigo accent, amber cut blocks.
enum Theme {
    static let bgWindow   = Color(hex: 0x0a0a0b)
    static let bgPanel    = Color(hex: 0x161618)
    static let bgElevated = Color(hex: 0x1e1e21)
    static let border     = Color(hex: 0x2a2a2e)
    static let text       = Color(hex: 0xf5f5f7)
    static let textDim    = Color(hex: 0x9b9ba3)
    static let textFaint  = Color(hex: 0x5d5d66)
    static let accent     = Color(hex: 0x5b5bd6)   // indigo
    static let accentSoft = Color(hex: 0x5b5bd6, alpha: 0.13)
    static let clip       = Color(hex: 0xd99136)   // amber
    static let clipBorder = Color(hex: 0xf0b45e)
    static let danger     = Color(hex: 0xe5484d)

    static let radiusBlock: CGFloat = 10
    static let radiusButton: CGFloat = 8
    static let radiusPanel: CGFloat = 14
    static let trackHeight: CGFloat = 56
}

extension Color {
    init(hex: UInt32, alpha: Double = 1) {
        self.init(
            .sRGB,
            red: Double((hex >> 16) & 0xff) / 255,
            green: Double((hex >> 8) & 0xff) / 255,
            blue: Double(hex & 0xff) / 255,
            opacity: alpha)
    }
}
