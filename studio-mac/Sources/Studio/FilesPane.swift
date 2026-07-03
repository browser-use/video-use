import SwiftUI

struct VideoFile: Identifiable, Equatable {
    var path: String
    var name: String
    var id: String { path }
}

// Left sidebar: every video file in the videos dir, each with a source-timeline strip showing
// exactly which ranges the current cut keeps (amber). Unused files are dimmed. "Here's everything
// I shot, here's what the agent kept." Click a kept segment to select that slice + seek to it.
struct FilesPane: View {
    @EnvironmentObject var store: Store

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text("SOURCE FILES").font(.system(size: 11, weight: .bold)).tracking(0.8)
                    .foregroundColor(Theme.textDim)
                Spacer()
                Text("\(store.videoFiles.count)")
                    .font(.system(size: 11, design: .monospaced)).foregroundColor(Theme.textFaint)
            }
            .padding(.horizontal, 14).padding(.vertical, 12)

            Divider().background(Theme.border)

            ScrollView {
                LazyVStack(spacing: 1) {
                    ForEach(store.videoFiles) { f in FileRow(file: f) }
                }
                .padding(.vertical, 4)
            }
        }
        .frame(width: 252)
        .background(Theme.bgPanel)
        .overlay(Rectangle().frame(width: 1).foregroundColor(Theme.border), alignment: .trailing)
    }
}

private struct FileRow: View {
    @EnvironmentObject var store: Store
    let file: VideoFile

    var body: some View {
        let used = store.ranges(forFile: file.path)
        let dur = store.fileDurations[file.path]
        let isSelected = used.contains { $0.index == store.selection }

        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 6) {
                Circle().fill(used.isEmpty ? Theme.textFaint : Theme.clip).frame(width: 6, height: 6)
                Text(file.name)
                    .font(.system(size: 12, weight: .medium))
                    .foregroundColor(Theme.text)
                    .lineLimit(1).truncationMode(.middle)
                Spacer(minLength: 4)
                Text(dur.map { durLabel($0) } ?? "…")
                    .font(.system(size: 10, design: .monospaced))
                    .foregroundColor(Theme.textFaint)
            }
            SourceStrip(fileDuration: dur, used: used)
        }
        .padding(.horizontal, 12).padding(.vertical, 8)
        .background(isSelected ? Theme.accentSoft : Color.clear)
        .opacity(used.isEmpty ? 0.5 : 1)
        .contentShape(Rectangle())
    }

    private func durLabel(_ s: Double) -> String {
        let m = Int(s) / 60, sec = Int(s) % 60
        return String(format: "%d:%02d", m, sec)
    }
}

/// A file's full source timeline (0…duration) with amber blocks where the cut uses it.
private struct SourceStrip: View {
    @EnvironmentObject var store: Store
    let fileDuration: Double?
    let used: [(index: Int, range: Range)]

    var body: some View {
        GeometryReader { geo in
            let w = geo.size.width
            ZStack(alignment: .leading) {
                RoundedRectangle(cornerRadius: 3).fill(Theme.bgElevated)
                if let dur = fileDuration, dur > 0 {
                    ForEach(used, id: \.index) { item in
                        let x = CGFloat(item.range.start / dur) * w
                        let bw = max(CGFloat(item.range.duration / dur) * w, 2)
                        RoundedRectangle(cornerRadius: 3)
                            .fill(store.selection == item.index ? Theme.clipBorder : Theme.clip)
                            .frame(width: bw)
                            .offset(x: min(x, w - bw))
                            .onTapGesture {
                                store.select(item.index)
                                store.seekToSegment(item.index)
                            }
                    }
                }
            }
        }
        .frame(height: 10)
    }
}
