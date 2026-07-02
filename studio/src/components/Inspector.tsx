import type { Edl } from "../types";
import { basename } from "../lib/project";
import { fmtTime, totalDuration } from "../lib/virtualTime";

interface Props {
  edl: Edl;
  selection: number | null;
  onSelect(i: number | null): void;
  onDelete(i: number): void;
  onGrade(preset: string): void;
}

const GRADE_PRESETS = ["none", "neutral_punch", "warm_cinematic"];

export default function Inspector({ edl, selection, onSelect, onDelete, onGrade }: Props) {
  const seg = selection != null ? edl.ranges[selection] : null;

  if (seg && selection != null) {
    return (
      <aside className="inspector">
        <div className="insp-head">
          <button className="btn btn-ghost" onClick={() => onSelect(null)} title="Close (Esc)">
            ←
          </button>
          <h2>Slice {selection + 1}</h2>
          {seg.beat && <span className="beat-chip">{seg.beat}</span>}
        </div>
        <div className="insp-rows">
          <div className="insp-row">
            <span className="insp-label">Source</span>
            <span className="insp-value" title={edl.sources[seg.source]}>
              {seg.source}
              <span className="insp-sub"> · {basename(edl.sources[seg.source] ?? "")}</span>
            </span>
          </div>
          <div className="insp-row">
            <span className="insp-label">In → Out</span>
            <span className="insp-value mono">
              {seg.start.toFixed(2)}s → {seg.end.toFixed(2)}s
              <span className="insp-sub"> · {(seg.end - seg.start).toFixed(2)}s</span>
            </span>
          </div>
        </div>
        {seg.quote && <blockquote className="insp-quote">“{seg.quote}”</blockquote>}
        {seg.reason && (
          <div className="insp-reason">
            <span className="insp-label">Agent's reason</span>
            <p>{seg.reason}</p>
          </div>
        )}
        <button className="btn btn-danger insp-remove" onClick={() => onDelete(selection)}>
          Remove slice
          <span className="kbd">⌫</span>
        </button>
      </aside>
    );
  }

  const gradeValue = edl.grade ?? "none";
  const options = GRADE_PRESETS.includes(gradeValue) ? GRADE_PRESETS : [...GRADE_PRESETS, gradeValue];
  const segCounts = new Map<string, number>();
  for (const r of edl.ranges) segCounts.set(r.source, (segCounts.get(r.source) ?? 0) + 1);

  return (
    <aside className="inspector">
      <div className="insp-head">
        <h2>Project</h2>
      </div>
      <div className="insp-rows">
        <div className="insp-row">
          <span className="insp-label">Grade</span>
          <select className="insp-select" value={gradeValue} onChange={(e) => onGrade(e.target.value)}>
            {options.map((o) => (
              <option key={o} value={o}>
                {o}
              </option>
            ))}
          </select>
        </div>
        <div className="insp-row">
          <span className="insp-label">Subtitles</span>
          <span className="insp-value" title={edl.subtitles}>
            {edl.subtitles ? basename(edl.subtitles) : "—"}
          </span>
        </div>
        <div className="insp-row">
          <span className="insp-label">Duration</span>
          <span className="insp-value mono">
            {fmtTime(totalDuration(edl.ranges))}
            <span className="insp-sub"> · {edl.ranges.length} slices</span>
          </span>
        </div>
        <div className="insp-row">
          <span className="insp-label">Overlays</span>
          <span className="insp-value">{edl.overlays?.length ?? 0}</span>
        </div>
      </div>
      <div className="insp-sources">
        <span className="insp-label">Sources</span>
        {Object.entries(edl.sources ?? {}).map(([key, path]) => (
          <div key={key} className="insp-source" title={path}>
            <span className="insp-source-key">{key}</span>
            <span className="insp-source-file">{basename(path)}</span>
            <span className="insp-sub">{segCounts.get(key) ?? 0}×</span>
          </div>
        ))}
      </div>
    </aside>
  );
}
