import SwiftUI

// Right inspector. No selection -> project panel (grade, subtitles, duration, source list).
// A selected slice -> slice editor (beat, source, in/out steppers, quote, agent's reason, remove).
struct InspectorView: View {
    @EnvironmentObject var store: Store

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                if let i = store.selection, i < store.edl.ranges.count {
                    sliceEditor(index: i, range: store.edl.ranges[i])
                } else {
                    projectPanel
                }
            }
            .padding(16)
        }
        .frame(width: 360)
        .background(Theme.bgPanel)
        .overlay(Rectangle().frame(width: 1).foregroundColor(Theme.border), alignment: .leading)
    }

    // MARK: project panel

    private var projectPanel: some View {
        VStack(alignment: .leading, spacing: 18) {
            sectionHeader("PROJECT")
            infoRow("Name", store.dir.map { Project.projectName(dir: $0) } ?? "—")
            infoRow("Grade", store.edl.grade ?? "none")
            infoRow("Subtitles", store.edl.subtitles ?? "—")
            infoRow("Duration", VirtualTime.fmt(store.total))
            infoRow("Slices", "\(store.edl.ranges.count)")

            Divider().background(Theme.border)

            sectionHeader("SOURCES")
            VStack(alignment: .leading, spacing: 8) {
                ForEach(store.edl.sources.keys.sorted(), id: \.self) { key in
                    let path = store.sourcePaths[key] ?? ""
                    let exists = FileManager.default.fileExists(atPath: path)
                    HStack(spacing: 8) {
                        Circle().fill(exists ? Theme.accent : Theme.danger)
                            .frame(width: 7, height: 7)
                        Text(key).font(.system(size: 12, weight: .medium)).foregroundColor(Theme.text)
                        Spacer()
                        if let d = store.sourceDurations[key] {
                            Text(String(format: "%.0fs", d))
                                .font(.system(size: 11, design: .monospaced))
                                .foregroundColor(Theme.textFaint)
                        }
                    }
                }
            }

            if store.edl.ranges.isEmpty {
                Text("Select a clip in the timeline to edit it.")
                    .font(.system(size: 12))
                    .foregroundColor(Theme.textFaint)
                    .padding(.top, 8)
            }
        }
    }

    // MARK: slice editor

    private func sliceEditor(index i: Int, range r: Range) -> some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                sectionHeader("SLICE EDITOR")
                Spacer()
                Button { store.select(nil) } label: {
                    Image(systemName: "xmark").font(.system(size: 11, weight: .bold))
                }
                .buttonStyle(.plain)
                .foregroundColor(Theme.textDim)
            }

            HStack(spacing: 8) {
                Text((r.beat ?? "CLIP").uppercased())
                    .font(.system(size: 11, weight: .bold))
                    .foregroundColor(.black)
                    .padding(.horizontal, 8).padding(.vertical, 3)
                    .background(Theme.clip)
                    .clipShape(Capsule())
                Text(r.source)
                    .font(.system(size: 12, weight: .medium, design: .monospaced))
                    .foregroundColor(Theme.textDim)
            }

            stepper(label: "In", value: r.start,
                    dec: { store.nudgeIn(i, by: -0.05) }, inc: { store.nudgeIn(i, by: 0.05) })
            stepper(label: "Out", value: r.end,
                    dec: { store.nudgeOut(i, by: -0.05) }, inc: { store.nudgeOut(i, by: 0.05) })
            infoRow("Length", String(format: "%.2fs", r.duration))

            if let quote = r.quote, !quote.isEmpty {
                VStack(alignment: .leading, spacing: 4) {
                    label("QUOTE")
                    Text("\u{201C}\(quote)\u{201D}")
                        .font(.system(size: 13)).italic()
                        .foregroundColor(Theme.text)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }

            if let reason = r.reason, !reason.isEmpty {
                VStack(alignment: .leading, spacing: 4) {
                    label("AGENT'S REASON")
                    Text(reason)
                        .font(.system(size: 12))
                        .foregroundColor(Theme.textDim)
                        .fixedSize(horizontal: false, vertical: true)
                        .padding(10)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(Theme.bgElevated)
                        .cornerRadius(Theme.radiusButton)
                }
            }

            Button(role: .destructive) { store.deleteSlice(i) } label: {
                HStack { Image(systemName: "trash"); Text("Remove slice") }
                    .font(.system(size: 12, weight: .medium))
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 8)
            }
            .buttonStyle(.plain)
            .foregroundColor(Theme.danger)
            .background(Theme.danger.opacity(0.12))
            .cornerRadius(Theme.radiusButton)
            .padding(.top, 4)
        }
    }

    // MARK: bits

    private func stepper(label lbl: String, value: Double, dec: @escaping () -> Void, inc: @escaping () -> Void) -> some View {
        HStack {
            Text(lbl).font(.system(size: 12)).foregroundColor(Theme.textDim)
            Spacer()
            HStack(spacing: 0) {
                stepButton("minus", action: dec)
                Text(String(format: "%.2fs", value))
                    .font(.system(size: 12, weight: .medium, design: .monospaced))
                    .foregroundColor(Theme.text)
                    .frame(width: 66)
                stepButton("plus", action: inc)
            }
            .background(Theme.bgElevated)
            .cornerRadius(Theme.radiusButton)
            .overlay(RoundedRectangle(cornerRadius: Theme.radiusButton).stroke(Theme.border, lineWidth: 1))
        }
    }

    private func stepButton(_ symbol: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: symbol)
                .font(.system(size: 10, weight: .bold))
                .frame(width: 28, height: 26)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .foregroundColor(Theme.textDim)
    }

    private func sectionHeader(_ t: String) -> some View {
        Text(t).font(.system(size: 11, weight: .bold)).tracking(0.8).foregroundColor(Theme.textDim)
    }

    private func label(_ t: String) -> some View {
        Text(t).font(.system(size: 10, weight: .semibold)).tracking(0.6).foregroundColor(Theme.textFaint)
    }

    private func infoRow(_ k: String, _ v: String) -> some View {
        HStack {
            Text(k).font(.system(size: 12)).foregroundColor(Theme.textDim)
            Spacer()
            Text(v).font(.system(size: 12, weight: .medium)).foregroundColor(Theme.text)
        }
    }
}
