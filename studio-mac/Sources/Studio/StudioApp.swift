import AppKit
import SwiftUI
import UniformTypeIdentifiers

// AppDelegate so the control server and initial project load happen at process launch,
// independent of any SwiftUI view appearing.
final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        Store.shared.bootstrap()
        NSApp.activate(ignoringOtherApps: true)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
}

@main
struct StudioApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var appDelegate
    @ObservedObject private var store = Store.shared

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(store)
                .frame(minWidth: 1000, minHeight: 640)
                .background(Theme.bgWindow)
        }
        .windowStyle(.hiddenTitleBar)
        .defaultSize(width: 1320, height: 820)
        .commands {
            CommandGroup(replacing: .newItem) {
                Button("Open EDL…") { openPanel() }
                    .keyboardShortcut("o", modifiers: .command)
            }
            CommandGroup(replacing: .undoRedo) {
                Button("Undo") { store.undo() }
                    .keyboardShortcut("z", modifiers: .command).disabled(!store.canUndo)
                Button("Redo") { store.redo() }
                    .keyboardShortcut("z", modifiers: [.command, .shift]).disabled(!store.canRedo)
            }
            CommandGroup(after: .toolbar) {
                Button("Export") { store.export(preview: false) }
                    .keyboardShortcut("e", modifiers: .command)
                Button("Export Preview") { store.export(preview: true) }
                    .keyboardShortcut("e", modifiers: [.command, .option])
                Button(store.playing ? "Pause" : "Play") { store.togglePlay() }
                    .keyboardShortcut(.space, modifiers: [])
            }
        }
    }

    private func openPanel() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [UTType.json]
        panel.allowsMultipleSelection = false
        if panel.runModal() == .OK, let url = panel.url {
            store.open(path: url.path)
        }
    }
}
