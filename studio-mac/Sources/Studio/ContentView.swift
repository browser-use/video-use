import SwiftUI

struct ContentView: View {
    @EnvironmentObject var store: Store

    var body: some View {
        VStack(spacing: 0) {
            TitleBar()
            Divider().background(Theme.border)
            HStack(spacing: 0) {
                VStack(spacing: 0) {
                    canvas
                    transport
                    TimelineView()
                }
                InspectorView()
            }
        }
        .background(Theme.bgWindow)
        .foregroundColor(Theme.text)
        .sheet(isPresented: $store.showExport) { ExportSheet() }
    }

    private var canvas: some View {
        ZStack {
            Theme.bgWindow
            if store.edlPath == nil {
                emptyState
            } else {
                PlayerView(player: store.player)
                    .aspectRatio(16.0 / 9.0, contentMode: .fit)
                    .padding(24)
                    .shadow(color: .black.opacity(0.5), radius: 24, y: 8)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private var emptyState: some View {
        VStack(spacing: 10) {
            Image(systemName: "film.stack").font(.system(size: 42)).foregroundColor(Theme.textFaint)
            Text("video-use Studio").font(.system(size: 16, weight: .semibold)).foregroundColor(Theme.textDim)
            Text(store.loadError ?? "Open an edl.json to begin")
                .font(.system(size: 12)).foregroundColor(Theme.textFaint)
        }
    }

    private var transport: some View {
        HStack(spacing: 18) {
            Spacer()
            transportButton("backward.end.fill") { store.seek(to: 0) }
            Button(action: store.togglePlay) {
                Image(systemName: store.playing ? "pause.fill" : "play.fill")
                    .font(.system(size: 16))
                    .frame(width: 40, height: 40)
                    .background(Theme.accent)
                    .foregroundColor(.white)
                    .clipShape(Circle())
            }
            .buttonStyle(.plain)
            transportButton("forward.end.fill") { store.seek(to: store.total) }
            Spacer()
            Text("\(VirtualTime.fmt(store.playhead))  /  \(VirtualTime.fmt(store.total))")
                .font(.system(size: 12, weight: .medium, design: .monospaced))
                .foregroundColor(Theme.textDim)
                .padding(.trailing, 8)
        }
        .padding(.horizontal, 16)
        .frame(height: 56)
        .background(Theme.bgWindow)
    }

    private func transportButton(_ symbol: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: symbol).font(.system(size: 14))
                .foregroundColor(Theme.textDim).frame(width: 32, height: 32)
        }
        .buttonStyle(.plain)
    }
}

struct TitleBar: View {
    @EnvironmentObject var store: Store

    var body: some View {
        HStack(spacing: 12) {
            Text(store.dir.map { Project.projectName(dir: $0) } ?? "video-use Studio")
                .font(.system(size: 13, weight: .semibold))
                .foregroundColor(Theme.text)

            HStack(spacing: 5) {
                Circle().fill(Theme.accent).frame(width: 7, height: 7)
                Text("agent synced").font(.system(size: 11)).foregroundColor(Theme.textDim)
            }
            .opacity(store.agentSynced ? 1 : 0)

            Spacer()

            iconButton("arrow.uturn.backward", enabled: store.canUndo) { store.undo() }
            iconButton("arrow.uturn.forward", enabled: store.canRedo) { store.redo() }

            Button { store.export(preview: false) } label: {
                HStack(spacing: 6) {
                    Image(systemName: "square.and.arrow.up").font(.system(size: 11, weight: .semibold))
                    Text("Export").font(.system(size: 12, weight: .semibold))
                }
                .foregroundColor(.white)
                .padding(.horizontal, 12).padding(.vertical, 6)
                .background(Theme.accent)
                .cornerRadius(Theme.radiusButton)
            }
            .buttonStyle(.plain)
            .help("Render final.mp4 (\u{2325}-click for preview)")
        }
        .padding(.leading, 78)   // clear the traffic-light inset
        .padding(.trailing, 16)
        .frame(height: 44)
        .background(Theme.bgWindow)
    }

    private func iconButton(_ symbol: String, enabled: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: symbol).font(.system(size: 13))
                .foregroundColor(enabled ? Theme.textDim : Theme.textFaint)
                .frame(width: 28, height: 28)
        }
        .buttonStyle(.plain)
        .disabled(!enabled)
    }
}

struct ExportSheet: View {
    @EnvironmentObject var store: Store

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text(store.exportRunning ? "Rendering…" : "Render complete")
                    .font(.system(size: 14, weight: .semibold))
                Spacer()
                if store.exportRunning { ProgressView().scaleEffect(0.6) }
            }
            ScrollView {
                Text(store.exportOutput)
                    .font(.system(size: 11, design: .monospaced))
                    .foregroundColor(Theme.textDim)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .textSelection(.enabled)
            }
            .frame(width: 560, height: 300)
            .background(Theme.bgWindow)
            .cornerRadius(8)
            HStack {
                Spacer()
                Button("Close") { store.showExport = false }
                    .disabled(store.exportRunning)
            }
        }
        .padding(20)
        .background(Theme.bgPanel)
    }
}
