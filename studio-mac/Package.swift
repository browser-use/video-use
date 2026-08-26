// swift-tools-version: 5.9
import PackageDescription

// Native macOS video-use Studio. Executable SwiftUI app, no Xcode project.
// The AVFoundation composition (Composition.swift) is the whole point: the EDL's
// ranges become one gapless AVMutableComposition handed to a single AVPlayer.
let package = Package(
    name: "VideoUseStudio",
    platforms: [.macOS(.v14)],
    targets: [
        .executableTarget(
            name: "Studio",
            path: "Sources/Studio"
        )
    ]
)
