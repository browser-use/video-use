import SwiftUI

// Right inspector. No selection -> project panel (grade, subtitles, duration, source list).
// A selected slice -> slice editor (beat, source, in/out steppers, quote, agent's reason, remove).
struct InspectorView: View {
    @EnvironmentObject var store: Store

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                nowPlaying           // read-only context for the clip under the playhead
                Divider().background(Theme.border)
                subtitleControls     // the main thing you actually tune here
                Divider().background(Theme.border)
                projectPanel
            }
            .padding(16)
        }
        .frame(width: 340)
        .background(Theme.bgPanel)
        .overlay(Rectangle().frame(width: 1).foregroundColor(Theme.border), alignment: .leading)
    }

    // MARK: now playing (read-only — the cut is edited by chat, not here)

    private var nowPlaying: some View {
        let i = VirtualTime.segmentAtOutput(store.offsets, store.playhead)
        let r = (i != nil && i! < store.edl.ranges.count) ? store.edl.ranges[i!] : nil
        return VStack(alignment: .leading, spacing: 8) {
            sectionHeader("NOW PLAYING")
            if let r {
                HStack(spacing: 8) {
                    Text((r.beat ?? "CLIP").uppercased())
                        .font(.system(size: 11, weight: .bold)).foregroundColor(.black)
                        .padding(.horizontal, 8).padding(.vertical, 3)
                        .background(Theme.clip).clipShape(Capsule())
                    Text(r.source).font(.system(size: 12, weight: .medium, design: .monospaced))
                        .foregroundColor(Theme.textDim)
                    Spacer()
                    Text(String(format: "%.1fs", r.duration))
                        .font(.system(size: 11, design: .monospaced)).foregroundColor(Theme.textFaint)
                }
                if let quote = r.quote, !quote.isEmpty {
                    Text("\u{201C}\(quote)\u{201D}")
                        .font(.system(size: 13)).italic().foregroundColor(Theme.text)
                        .fixedSize(horizontal: false, vertical: true)
                }
            } else {
                Text("—").foregroundColor(Theme.textFaint)
            }
        }
    }

    // MARK: project panel

    private var projectPanel: some View {
        VStack(alignment: .leading, spacing: 18) {
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

            Divider().background(Theme.border)

            sectionHeader("PROJECT")
            infoRow("Grade", store.edl.grade ?? "none")
            infoRow("Duration", VirtualTime.fmt(store.total))
            infoRow("Clips", "\(store.edl.ranges.count)")
        }
    }

    // MARK: subtitle style controls

    private var subtitleControls: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                sectionHeader("SUBTITLES")
                Spacer()
                Toggle("", isOn: styleBinding(\.enabled)).labelsHidden()
                    .toggleStyle(.switch).controlSize(.mini).tint(Theme.accent)
            }

            if store.subtitleStyle.enabled {
                HStack {
                    Text("Uppercase").font(.system(size: 12)).foregroundColor(Theme.textDim)
                    Spacer()
                    Toggle("", isOn: styleBinding(\.uppercase)).labelsHidden()
                        .toggleStyle(.switch).controlSize(.mini).tint(Theme.accent)
                }
                HStack {
                    Text("Words / line").font(.system(size: 12)).foregroundColor(Theme.textDim)
                    Spacer()
                    Picker("", selection: chunkBinding) {
                        Text("1").tag(1); Text("2").tag(2); Text("3").tag(3)
                    }
                    .pickerStyle(.segmented).labelsHidden().frame(width: 108)
                }
                sliderRow("Size", keyPath: \.size, range: 12...160, suffix: "")
                sliderRow("Bottom margin", keyPath: \.margin_v, range: 0...220, suffix: "")
            } else {
                Text("Captions hidden. Enable to preview and export burn-in.")
                    .font(.system(size: 11)).foregroundColor(Theme.textFaint)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    /// Discrete control binding (toggle) — commits immediately (persist + log).
    private func styleBinding(_ kp: WritableKeyPath<SubtitleStyle, Bool>) -> Binding<Bool> {
        Binding(
            get: { store.subtitleStyle[keyPath: kp] },
            set: { var s = store.subtitleStyle; s[keyPath: kp] = $0; store.updateSubtitleStyle(s, commit: true) })
    }

    private var chunkBinding: Binding<Int> {
        Binding(
            get: { store.subtitleStyle.chunk_words },
            set: { var s = store.subtitleStyle; s.chunk_words = $0; store.updateSubtitleStyle(s, commit: true) })
    }

    /// Slider with a live in-memory preview during drag and a single commit (persist + log) on release.
    private func sliderRow(_ label: String, keyPath kp: WritableKeyPath<SubtitleStyle, Double>,
                           range: ClosedRange<Double>, suffix: String) -> some View {
        let live = Binding<Double>(
            get: { store.subtitleStyle[keyPath: kp] },
            set: { var s = store.subtitleStyle; s[keyPath: kp] = $0; store.updateSubtitleStyle(s, commit: false) })
        return VStack(alignment: .leading, spacing: 2) {
            HStack {
                Text(label).font(.system(size: 12)).foregroundColor(Theme.textDim)
                Spacer()
                Text(String(format: "%.0f%@", store.subtitleStyle[keyPath: kp], suffix))
                    .font(.system(size: 11, design: .monospaced)).foregroundColor(Theme.text)
            }
            Slider(value: live, in: range, onEditingChanged: { editing in
                if !editing { store.updateSubtitleStyle(store.subtitleStyle, commit: true) }
            })
            .tint(Theme.accent)
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
