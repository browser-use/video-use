import SwiftUI

// Output-time timeline (Screen Studio style): contiguous amber cut blocks with beat labels,
// an indigo overlay track, a subtitle strip, a ruler, and the playhead. Click a block to
// select; click empty ruler space to seek; drag a selected block's edge to trim.
struct TimelineView: View {
    @EnvironmentObject var store: Store

    @State private var dragEdge: (index: Int, isStart: Bool)?
    @State private var dragTime: Double = 0

    var body: some View {
        GeometryReader { geo in
            let w = geo.size.width
            let total = max(store.total, 0.001)
            let scale = w / total
            let x: (Double) -> CGFloat = { CGFloat($0) * scale }

            ZStack(alignment: .topLeading) {
                Theme.bgPanel

                // Seek surface (ruler + background). Clicking seeks to that output time.
                Color.clear
                    .contentShape(Rectangle())
                    .gesture(DragGesture(minimumDistance: 0).onEnded { v in
                        store.seek(to: Double(v.location.x) / scale)
                    })

                VStack(alignment: .leading, spacing: 8) {
                    ruler(scale: scale, total: total)
                    clipTrack(scale: scale, x: x)
                    overlayTrack(scale: scale, x: x)
                    subtitleStrip
                }
                .padding(.horizontal, 0)
                .padding(.vertical, 12)

                playhead(x: x(store.playhead), height: geo.size.height)
            }
        }
        .frame(height: 150)
        .background(Theme.bgPanel)
        .overlay(Rectangle().frame(height: 1).foregroundColor(Theme.border), alignment: .top)
    }

    // MARK: ruler

    private func ruler(scale: CGFloat, total: Double) -> some View {
        let step = niceStep(total / 6)
        let count = Int(total / step)
        return ZStack(alignment: .topLeading) {
            ForEach(0...max(count, 0), id: \.self) { i in
                let t = Double(i) * step
                if t <= total {
                    Text(VirtualTime.fmt(t))
                        .font(.system(size: 9, design: .monospaced))
                        .foregroundColor(Theme.textFaint)
                        .fixedSize()
                        .offset(x: CGFloat(t) * scale + 2, y: 0)
                }
            }
        }
        .frame(height: 12, alignment: .topLeading)
    }

    // MARK: amber cut track

    private func clipTrack(scale: CGFloat, x: @escaping (Double) -> CGFloat) -> some View {
        ZStack(alignment: .topLeading) {
            ForEach(Array(store.edl.ranges.enumerated()), id: \.offset) { i, r in
                let selected = store.selection == i
                let left = x(store.offsets[i])
                let width = max(x(r.duration) - 2, 2)
                clipBlock(range: r, selected: selected)
                    .frame(width: width, height: Theme.trackHeight)
                    .overlay(alignment: .leading) { if selected { edgeHandle(index: i, isStart: true, scale: scale) } }
                    .overlay(alignment: .trailing) { if selected { edgeHandle(index: i, isStart: false, scale: scale) } }
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
                    Text(String(format: "%.1fs", r.duration))
                        .font(.system(size: 9, weight: .medium, design: .monospaced))
                        .foregroundColor(Color.black.opacity(0.55))
                }
                .padding(7)
            }
            .shadow(color: .black.opacity(0.25), radius: 3, y: 1)
    }

    private func edgeHandle(index: Int, isStart: Bool, scale: CGFloat) -> some View {
        RoundedRectangle(cornerRadius: 3)
            .fill(Theme.clipBorder)
            .frame(width: 6, height: Theme.trackHeight - 14)
            .padding(.horizontal, 2)
            .contentShape(Rectangle().inset(by: -6))
            .gesture(
                DragGesture(minimumDistance: 1)
                    .onEnded { v in
                        let deltaT = Double(v.translation.width) / Double(scale)
                        let r = store.edl.ranges[index]
                        if isStart { store.setIn(index, to: r.start + deltaT) }
                        else { store.setOut(index, to: r.end + deltaT) }
                    }
            )
    }

    // MARK: overlay + subtitle tracks

    private func overlayTrack(scale: CGFloat, x: @escaping (Double) -> CGFloat) -> some View {
        ZStack(alignment: .topLeading) {
            RoundedRectangle(cornerRadius: 4).fill(Theme.bgElevated.opacity(0.5))
                .frame(height: 14)
            ForEach(Array((store.edl.overlays ?? []).enumerated()), id: \.offset) { _, ov in
                RoundedRectangle(cornerRadius: 4)
                    .fill(Theme.accent)
                    .frame(width: max(x(ov.duration), 4), height: 14)
                    .offset(x: x(ov.start_in_output))
            }
        }
        .frame(height: 14, alignment: .topLeading)
    }

    private var subtitleStrip: some View {
        HStack(spacing: 4) {
            RoundedRectangle(cornerRadius: 3)
                .fill(store.edl.subtitles != nil ? Theme.accent.opacity(0.35) : Theme.bgElevated)
                .frame(height: 6)
            Text(store.edl.subtitles != nil ? "subtitles" : "no subtitles")
                .font(.system(size: 8))
                .foregroundColor(Theme.textFaint)
                .fixedSize()
        }
        .frame(height: 8)
    }

    // MARK: playhead

    private func playhead(x: CGFloat, height: CGFloat) -> some View {
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
        .offset(x: x - 0.75)
        .allowsHitTesting(false)
    }

    private func niceStep(_ raw: Double) -> Double {
        guard raw > 0 else { return 5 }
        let candidates = [1.0, 2, 5, 10, 15, 30, 60, 120, 300]
        return candidates.first(where: { $0 >= raw }) ?? 300
    }
}
