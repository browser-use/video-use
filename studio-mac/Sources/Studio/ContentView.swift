import AppKit
import SwiftUI

struct ContentView: View {
    @EnvironmentObject var store: Store

    var body: some View {
        VStack(spacing: 0) {
            TitleBar()
            Divider().background(Theme.border)
            HStack(spacing: 0) {
                if store.showFiles { FilesPane().transition(.move(edge: .leading)) }
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
                    .aspectRatio(store.renderAspect, contentMode: .fit)
                    .overlay {
                        if let caption = store.currentCaption {
                            SubtitleOverlay(text: caption, style: store.subtitleStyle)
                        }
                    }
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
            Button {
                withAnimation(.easeOut(duration: 0.2)) { store.showFiles.toggle() }
            } label: {
                Image(systemName: "sidebar.left").font(.system(size: 13))
                    .foregroundColor(store.showFiles ? Theme.accent : Theme.textDim)
                    .frame(width: 26, height: 26)
            }
            .buttonStyle(.plain)
            .help("Toggle source files")

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
    @State private var showLog = false

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            if store.exportSucceeded == true {
                successView
            } else if store.exportSucceeded == false {
                failureView
            } else {
                runningView
            }
        }
        .padding(24)
        .frame(width: 460)
        .background(Theme.bgPanel)
    }

    // MARK: running

    private var runningView: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(spacing: 10) {
                ProgressView().controlSize(.small)
                Text("Exporting").font(.system(size: 15, weight: .semibold)).foregroundColor(Theme.text)
                Spacer()
            }
            IndeterminateBar()
            Text(store.exportStage)
                .font(.system(size: 12)).foregroundColor(Theme.textDim)
                .animation(.easeInOut(duration: 0.2), value: store.exportStage)

            logDisclosure

            HStack {
                Spacer()
                Button("Cancel") { store.cancelExport() }.buttonStyle(.plain)
                    .foregroundColor(Theme.textDim)
            }
        }
    }

    // MARK: success

    private var successView: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(spacing: 10) {
                Image(systemName: "checkmark.circle.fill").foregroundColor(Theme.accent).font(.system(size: 18))
                Text("Export complete").font(.system(size: 15, weight: .semibold)).foregroundColor(Theme.text)
                Spacer()
            }
            VStack(spacing: 0) {
                metaRow("File", (store.exportOutPath.map { ($0 as NSString).lastPathComponent }) ?? "—")
                divider
                metaRow("Size", store.exportSizeMB.map { String(format: "%.1f MB", $0) } ?? "—")
                divider
                metaRow("Duration", store.exportDuration.map { VirtualTime.fmt($0) } ?? "—")
            }
            .background(Theme.bgElevated)
            .cornerRadius(Theme.radiusButton)

            HStack(spacing: 10) {
                Spacer()
                Button("Reveal in Finder") { revealInFinder() }
                    .buttonStyle(.plain)
                    .foregroundColor(Theme.text)
                    .padding(.horizontal, 12).padding(.vertical, 7)
                    .background(Theme.bgElevated).cornerRadius(Theme.radiusButton)
                Button("Done") { store.showExport = false }
                    .buttonStyle(.plain)
                    .foregroundColor(.white)
                    .padding(.horizontal, 16).padding(.vertical, 7)
                    .background(Theme.accent).cornerRadius(Theme.radiusButton)
            }
        }
    }

    // MARK: failure

    private var failureView: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(spacing: 10) {
                Image(systemName: "exclamationmark.triangle.fill").foregroundColor(Theme.danger).font(.system(size: 18))
                Text("Export failed").font(.system(size: 15, weight: .semibold)).foregroundColor(Theme.text)
                Spacer()
            }
            Text(store.exportError ?? "Something went wrong.")
                .font(.system(size: 12, design: .monospaced))
                .foregroundColor(Theme.danger)
                .fixedSize(horizontal: false, vertical: true)
                .padding(10)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Theme.danger.opacity(0.1))
                .cornerRadius(Theme.radiusButton)

            logView.frame(height: 200)

            HStack {
                Spacer()
                Button("Done") { store.showExport = false }
                    .buttonStyle(.plain).foregroundColor(.white)
                    .padding(.horizontal, 16).padding(.vertical, 7)
                    .background(Theme.accent).cornerRadius(Theme.radiusButton)
            }
        }
    }

    // MARK: bits

    private var logDisclosure: some View {
        DisclosureGroup(isExpanded: $showLog) {
            logView.frame(height: 180).padding(.top, 6)
        } label: {
            Text("Show log").font(.system(size: 12)).foregroundColor(Theme.textDim)
        }
        .disclosureGroupStyle(.automatic)
        .tint(Theme.textDim)
    }

    private var logView: some View {
        ScrollView {
            Text(store.exportOutput)
                .font(.system(size: 11, design: .monospaced))
                .foregroundColor(Theme.textDim)
                .frame(maxWidth: .infinity, alignment: .leading)
                .textSelection(.enabled)
                .padding(8)
        }
        .background(Theme.bgWindow)
        .cornerRadius(Theme.radiusButton)
    }

    private var divider: some View { Rectangle().fill(Theme.border).frame(height: 1) }

    private func metaRow(_ k: String, _ v: String) -> some View {
        HStack {
            Text(k).font(.system(size: 12)).foregroundColor(Theme.textDim)
            Spacer()
            Text(v).font(.system(size: 12, weight: .medium, design: .monospaced))
                .foregroundColor(Theme.text).lineLimit(1).truncationMode(.middle)
        }
        .padding(.horizontal, 12).padding(.vertical, 9)
    }

    private func revealInFinder() {
        guard let p = store.exportOutPath else { return }
        NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: p)])
    }
}

/// A slim indeterminate progress bar in the app's indigo, since render.py emits no percentage.
private struct IndeterminateBar: View {
    @State private var phase: CGFloat = 0
    var body: some View {
        GeometryReader { geo in
            let w = geo.size.width
            RoundedRectangle(cornerRadius: 3).fill(Theme.bgElevated)
                .overlay(alignment: .leading) {
                    RoundedRectangle(cornerRadius: 3).fill(Theme.accent)
                        .frame(width: w * 0.3)
                        .offset(x: phase * w * 1.3 - w * 0.3)
                }
                .clipShape(RoundedRectangle(cornerRadius: 3))
        }
        .frame(height: 4)
        .onAppear {
            withAnimation(.easeInOut(duration: 1.1).repeatForever(autoreverses: false)) { phase = 1 }
        }
    }
}
