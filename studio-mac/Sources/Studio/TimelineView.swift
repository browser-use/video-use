import SwiftUI

// Output-time timeline (Screen Studio style): contiguous amber cut blocks with beat labels,
// an indigo overlay track, a subtitle strip, a ruler, and the playhead.
// Zoom: default = fit whole duration to width; pinch (or +/-) to zoom in. When zoomed in,
// hover near the left/right edge to pan continuously (speed scales with edge depth), and the
// view auto-follows the playhead during playback. Click a block to select; click empty space
// to seek; drag a selected block's edge to trim.
struct TimelineView: View {
    @EnvironmentObject var store: Store

    @State private var zoom: Double = 1        // 1 = fit-to-width (minimum)
    @State private var zoomBase: Double = 1
    @State private var panOffset: Double = 0   // points scrolled from the left
    @State private var viewWidth: CGFloat = 1

    private var pps: Double { (Double(viewWidth) / max(store.total, 0.001)) * zoom }
    private var contentW: Double { store.total * pps }
    private var maxPan: Double { max(0, contentW - Double(viewWidth)) }
    private var zoomedIn: Bool { contentW > Double(viewWidth) + 0.5 }
    private func x(_ t: Double) -> CGFloat { CGFloat(t * pps - panOffset) }
    private func timeAt(_ px: CGFloat) -> Double { (Double(px) + panOffset) / pps }

    var body: some View {
        GeometryReader { geo in
            ZStack(alignment: .topLeading) {
                Theme.bgPanel

                // Seek surface — click or drag scrubs the video live.
                Color.clear
                    .contentShape(Rectangle())
                    .gesture(DragGesture(minimumDistance: 0)
                        .onChanged { v in store.seek(to: timeAt(v.location.x), select: false, precise: false) }
                        .onEnded { v in store.seek(to: timeAt(v.location.x)) })

                VStack(alignment: .leading, spacing: 8) {
                    ruler()
                    clipTrack()
                    captionTrack()
                }
                .padding(.vertical, 12)

                playhead(height: geo.size.height)
                zoomControls
            }
            .clipped()
            .onAppear { viewWidth = geo.size.width }
            .onChange(of: geo.size.width) { _, w in viewWidth = w; clampPan() }
        }
        .frame(height: 172)
        .background(Theme.bgPanel)
        .overlay(Rectangle().frame(height: 1).foregroundColor(Theme.border), alignment: .top)
        .gesture(
            MagnificationGesture()
                .onChanged { applyZoom(zoomBase * $0) }
                .onEnded { _ in zoomBase = zoom }
        )
        // Hover-scrub: moving the mouse over the timeline scrubs the video to that spot (when paused).
        .onContinuousHover { phase in
            if case .active(let p) = phase, !store.playing {
                store.seek(to: timeAt(p.x), select: false, precise: false)
            }
        }
        .onChange(of: store.playhead) { _, _ in followPlayhead() }
        .onChange(of: store.total) { _, _ in clampPan() }
    }

    // MARK: zoom / pan behavior

    private func applyZoom(_ z: Double) {
        let center = timeAt(viewWidth / 2)              // keep viewport center fixed
        zoom = min(max(z, 1), 40)
        panOffset = min(max(center * pps - Double(viewWidth) / 2, 0), maxPan)
    }

    private func clampPan() { panOffset = min(max(panOffset, 0), maxPan) }

    private func followPlayhead() {
        guard zoomedIn else { return }
        let px = x(store.playhead)
        let lo = viewWidth * 0.12, hi = viewWidth * 0.88
        if px < lo || px > hi {
            panOffset = min(max(store.playhead * pps - Double(viewWidth) * 0.3, 0), maxPan)
        }
    }

    private var zoomControls: some View {
        HStack(spacing: 4) {
            zoomButton("minus.magnifyingglass") { applyZoom(zoom / 1.4); zoomBase = zoom }
            zoomButton("arrow.left.and.right") { zoom = 1; zoomBase = 1; panOffset = 0 }   // fit
            zoomButton("plus.magnifyingglass") { applyZoom(zoom * 1.4); zoomBase = zoom }
        }
        .padding(6)
        .background(Theme.bgWindow.opacity(0.8))
        .cornerRadius(8)
        .padding(8)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottomTrailing)
    }

    private func zoomButton(_ symbol: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: symbol).font(.system(size: 11)).foregroundColor(Theme.textDim)
                .frame(width: 22, height: 20).contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }

    // MARK: ruler

    private func ruler() -> some View {
        let total = max(store.total, 0.001)
        let step = niceStep(total / (6 * zoom))
        let count = Int(total / step)
        return ZStack(alignment: .topLeading) {
            ForEach(0...max(count, 0), id: \.self) { i in
                let t = Double(i) * step
                if t <= total {
                    Text(VirtualTime.fmt(t))
                        .font(.system(size: 9, design: .monospaced))
                        .foregroundColor(Theme.textFaint)
                        .fixedSize()
                        .offset(x: x(t) + 2, y: 0)
                }
            }
        }
        .frame(height: 12, alignment: .topLeading)
    }

    // MARK: amber cut track

    private func clipTrack() -> some View {
        ZStack(alignment: .topLeading) {
            ForEach(Array(store.edl.ranges.enumerated()), id: \.offset) { i, r in
                let selected = store.selection == i
                let left = x(store.offsets[i])
                let width = max(CGFloat(r.duration * pps) - 2, 2)
                clipBlock(range: r, selected: selected)
                    .frame(width: width, height: Theme.trackHeight)
                    .overlay(alignment: .leading) { if selected { edgeHandle(index: i, isStart: true) } }
                    .overlay(alignment: .trailing) { if selected { edgeHandle(index: i, isStart: false) } }
                    .offset(x: left + 1)
                    .onTapGesture {
                        store.select(i)
                        store.seekToSegment(i)
                    }
            }
        }
        .frame(height: Theme.trackHeight, alignment: .topLeading)
    }

    private func clipBlock(range r: Range, selected: Bool) -> some View {
        RoundedRectangle(cornerRadius: Theme.radiusBlock)
            .fill(LinearGradient(
                colors: [Theme.clip, Theme.clip.opacity(0.82)],
                startPoint: .top, endPoint: .bottom))
            .overlay(
                RoundedRectangle(cornerRadius: Theme.radiusBlock)
                    .stroke(selected ? Theme.clipBorder : Color.black.opacity(0.25),
                            lineWidth: selected ? 2 : 1))
            .overlay(alignment: .topLeading) {
                VStack(alignment: .leading, spacing: 2) {
                    Text((r.beat ?? "CLIP").uppercased())
                        .font(.system(size: 10, weight: .bold))
                        .foregroundColor(Color.black.opacity(0.8))
                        .lineLimit(1)
                    Text(String(format: "%.1fs", r.duration))
                        .font(.system(size: 9, weight: .medium, design: .monospaced))
                        .foregroundColor(Color.black.opacity(0.55))
                }
                .padding(7)
            }
            .shadow(color: .black.opacity(0.25), radius: 3, y: 1)
    }

    private func edgeHandle(index: Int, isStart: Bool) -> some View {
        RoundedRectangle(cornerRadius: 3)
            .fill(Theme.clipBorder)
            .frame(width: 6, height: Theme.trackHeight - 14)
            .padding(.horizontal, 2)
            .contentShape(Rectangle().inset(by: -6))
            .gesture(
                DragGesture(minimumDistance: 1)
                    .onEnded { v in
                        let deltaT = Double(v.translation.width) / pps
                        let r = store.edl.ranges[index]
                        if isStart { store.setIn(index, to: r.start + deltaT) }
                        else { store.setOut(index, to: r.end + deltaT) }
                    }
            )
    }

    // MARK: caption lane — the actual caption chunks, click to jump

    private func captionTrack() -> some View {
        ZStack(alignment: .topLeading) {
            RoundedRectangle(cornerRadius: 6).fill(Theme.bgElevated.opacity(0.35))
                .frame(height: 26)

            if store.cues.isEmpty || !store.subtitleStyle.enabled {
                Text(store.subtitleStyle.enabled ? "no captions" : "subtitles off")
                    .font(.system(size: 10)).foregroundColor(Theme.textFaint)
                    .padding(.leading, 8).frame(height: 26, alignment: .leading)
            } else {
                ForEach(Array(store.cues.enumerated()), id: \.offset) { _, c in
                    let left = x(c.start)
                    let w = max(x(c.end) - x(c.start) - 1, 3)
                    let active = store.playhead >= c.start && store.playhead < c.end
                    captionBlock(text: c.text, active: active, width: w)
                        .frame(width: w, height: 26)
                        .offset(x: left)
                        .onTapGesture { store.seek(to: c.start + 0.01) }
                }
            }
        }
        .frame(height: 26, alignment: .topLeading)
    }

    private func captionBlock(text: String, active: Bool, width: CGFloat) -> some View {
        RoundedRectangle(cornerRadius: 5)
            .fill(active ? Theme.accent : Theme.accent.opacity(0.5))
            .overlay(alignment: .leading) {
                if width > 26 {
                    Text(text)
                        .font(.system(size: 9, weight: .semibold))
                        .foregroundColor(.white)
                        .lineLimit(1)
                        .padding(.horizontal, 5)
                }
            }
            .overlay(RoundedRectangle(cornerRadius: 5).stroke(Color.white.opacity(active ? 0.5 : 0.12), lineWidth: 1))
    }

    // MARK: playhead

    private func playhead(height: CGFloat) -> some View {
        ZStack(alignment: .top) {
            Rectangle()
                .fill(Theme.accent)
                .frame(width: 1.5, height: height)
            Circle()
                .fill(Theme.accent)
                .frame(width: 11, height: 11)
                .overlay(Circle().stroke(Color.white.opacity(0.4), lineWidth: 1))
                .offset(y: -3)
        }
        .offset(x: x(store.playhead) - 0.75)
        .allowsHitTesting(false)
    }

    private func niceStep(_ raw: Double) -> Double {
        guard raw > 0 else { return 5 }
        let candidates = [0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300]
        return candidates.first(where: { $0 >= raw }) ?? 300
    }
}
