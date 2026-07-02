import { useEffect, useMemo, useRef, useState } from "react";
import "./Timeline.css";
import type { MouseEvent as ReactMouseEvent } from "react";
import type { Edl, EditLogEntry, Word } from "../types";
import { basename, logEntry } from "../lib/project";
import {
  clamp,
  segmentOffsets,
  snapToBoundary,
  sourceBoundaries,
  totalDuration,
  wordAtBoundary,
} from "../lib/virtualTime";

interface Props {
  edl: Edl;
  transcripts: Record<string, Word[]>;
  playhead: number;
  selection: number | null;
  onSeek(t: number): void;
  onSelect(i: number | null): void;
  onCommit(edl: Edl, entries: EditLogEntry[]): void;
  onDragging(active: boolean): void;
}

const MIN_DUR = 0.2;
const TICK_STEPS = [0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300];

type Drag =
  | { kind: "trim"; i: number; edge: "start" | "end"; value: number; word: string | null; x: number; y: number }
  | { kind: "move"; i: number; dx: number; drop: number };

export default function Timeline(props: Props) {
  const { edl, transcripts, playhead, selection, onSeek, onSelect, onCommit, onDragging } = props;
  const bodyRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  const [drag, setDrag] = useState<Drag | null>(null);

  useEffect(() => {
    const el = bodyRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setWidth(el.clientWidth));
    ro.observe(el);
    setWidth(el.clientWidth);
    return () => ro.disconnect();
  }, []);

  const ranges = edl.ranges;
  const total = Math.max(totalDuration(ranges), 0.001);
  // Fit-width zoom, frozen against the *committed* cut so blocks don't rescale mid-drag.
  const pps = width > 0 ? width / total : 0;

  // Draft layout reflects an in-progress trim; committed layout otherwise.
  const draft = useMemo(() => {
    if (drag?.kind !== "trim") return ranges;
    const next = [...ranges];
    next[drag.i] = { ...next[drag.i], [drag.edge]: drag.value };
    return next;
  }, [ranges, drag]);
  const offsets = useMemo(() => segmentOffsets(draft), [draft]);

  // Gesture handlers read the freshest scale through refs (window listeners outlive renders).
  const ppsRef = useRef(pps);
  ppsRef.current = pps;
  const offsetsRef = useRef(offsets);
  offsetsRef.current = offsets;

  const timeAt = (clientX: number) => {
    const rect = bodyRef.current?.getBoundingClientRect();
    if (!rect || ppsRef.current <= 0) return 0;
    return clamp((clientX - rect.left) / ppsRef.current, 0, total);
  };

  const beginSeek = (e: ReactMouseEvent) => {
    if (e.button !== 0) return;
    e.preventDefault();
    onSeek(timeAt(e.clientX));
    const move = (ev: MouseEvent) => onSeek(timeAt(ev.clientX));
    const up = () => {
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
  };

  const beginTrim = (e: ReactMouseEvent, i: number, edge: "start" | "end") => {
    if (e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    onDragging(true);
    const orig = ranges[i];
    const words = transcripts[orig.source];
    const bounds = words ? sourceBoundaries(words) : [];
    const before = edge === "start" ? orig.start : orig.end;
    const startX = e.clientX;
    let value = before;

    const move = (ev: MouseEvent) => {
      const dt = ppsRef.current > 0 ? (ev.clientX - startX) / ppsRef.current : 0;
      let v = before + dt;
      let word: string | null = null;
      if (bounds.length > 0) {
        v = snapToBoundary(bounds, v);
        const w = wordAtBoundary(words, v);
        // "…wasted.”" when cutting after a word; "“We…" when cutting before one.
        word = w ? (Math.abs(w.end - v) < 1e-6 ? `…${w.text}”` : `“${w.text}…`) : null;
      } else {
        v = Math.round(v * 100) / 100; // no transcript: free drag on a 0.01s grid
      }
      if (edge === "start") v = clamp(v, 0, orig.end - MIN_DUR);
      else v = Math.max(v, orig.start + MIN_DUR);
      value = v;
      setDrag({ kind: "trim", i, edge, value: v, word, x: ev.clientX, y: ev.clientY });
    };
    const up = () => {
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
      setDrag(null);
      onDragging(false);
      if (Math.abs(value - before) > 1e-4) {
        const next = [...ranges];
        next[i] = { ...orig, [edge]: value };
        onCommit({ ...edl, ranges: next }, [
          logEntry("trim", { segment: i, field: edge, before, after: value }),
        ]);
      }
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
  };

  const beginMove = (e: ReactMouseEvent, i: number) => {
    if (e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    const startX = e.clientX;
    const base = segmentOffsets(ranges);
    let moved = false;
    let drop = i;

    const move = (ev: MouseEvent) => {
      const dx = ev.clientX - startX;
      if (!moved && Math.abs(dx) < 4) return;
      if (!moved) {
        moved = true;
        onDragging(true);
      }
      // Drop index = how many *other* blocks the dragged block's center has passed.
      const center = ((base[i] + base[i + 1]) / 2) * ppsRef.current + dx;
      let d = 0;
      for (let j = 0; j < ranges.length; j++) {
        if (j === i) continue;
        if (center > ((base[j] + base[j + 1]) / 2) * ppsRef.current) d++;
      }
      drop = d;
      setDrag({ kind: "move", i, dx, drop: d });
    };
    const up = () => {
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
      setDrag(null);
      if (!moved) {
        onSelect(selection === i ? null : i);
        return;
      }
      onDragging(false);
      if (drop !== i) {
        const without = ranges.filter((_, j) => j !== i);
        const next = [...without.slice(0, drop), ranges[i], ...without.slice(drop)];
        onCommit({ ...edl, ranges: next }, [logEntry("reorder", { segment: i, to: drop })]);
        onSelect(drop);
      }
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
  };

  // Ruler ticks: coarsest step that keeps labels >= 70px apart.
  const step = TICK_STEPS.find((s) => s * pps >= 70) ?? TICK_STEPS[TICK_STEPS.length - 1];
  const ticks: number[] = [];
  if (pps > 0) for (let t = 0; t <= total + 1e-6; t += step) ticks.push(t);

  // Drop indicator x: boundary position within the layout minus the dragged block.
  let dropX: number | null = null;
  if (drag?.kind === "move") {
    let t = 0;
    let seen = 0;
    for (let j = 0; j < ranges.length && seen < drag.drop; j++) {
      if (j === drag.i) continue;
      t += ranges[j].end - ranges[j].start;
      seen++;
    }
    dropX = t * pps;
  }

  const playheadX = clamp(playhead, 0, total) * pps;

  return (
    <div className="timeline">
      <div className="tl-body" ref={bodyRef}>
        <div className="tl-ruler" onMouseDown={beginSeek}>
          {ticks.map((t) => (
            <div key={t} className="tl-tick" style={{ left: t * pps }}>
              <span>{fmtTick(t)}</span>
            </div>
          ))}
        </div>
        <div className="tl-clips" onMouseDown={beginSeek}>
          {draft.map((r, i) => {
            const isMoving = drag?.kind === "move" && drag.i === i;
            const dur = r.end - r.start;
            return (
              <div
                key={i}
                className={
                  "clip" + (selection === i ? " selected" : "") + (isMoving ? " dragging" : "")
                }
                style={{
                  left: offsets[i] * pps + 1,
                  width: Math.max(dur * pps - 2, 6),
                  transform: isMoving ? `translateX(${drag.dx}px)` : undefined,
                }}
                onMouseDown={(ev) => beginMove(ev, i)}
              >
                <div className="clip-label">
                  <span className="clip-beat">{r.beat ?? r.source}</span>
                  <span className="clip-dur">{dur.toFixed(1)}s</span>
                </div>
                <div className="clip-edge left" onMouseDown={(ev) => beginTrim(ev, i, "start")} />
                <div className="clip-edge right" onMouseDown={(ev) => beginTrim(ev, i, "end")} />
              </div>
            );
          })}
          {dropX != null && <div className="tl-drop" style={{ left: dropX }} />}
        </div>
        <div className="tl-overlays">
          {(edl.overlays ?? []).map((o, i) => (
            <div
              key={i}
              className="tl-ovl"
              style={{ left: o.start_in_output * pps, width: Math.max(o.duration * pps, 6) }}
              title={o.file}
            >
              {basename(o.file)}
            </div>
          ))}
        </div>
        {edl.subtitles && <div className="tl-subs" title={`Subtitles: ${edl.subtitles}`} />}
        <div className="tl-playhead" style={{ left: playheadX }}>
          <div className="tl-playhead-handle" onMouseDown={beginSeek} />
        </div>
      </div>
      {drag?.kind === "trim" && (
        <div className="snap-tip" style={{ left: drag.x + 14, top: drag.y - 40 }}>
          {drag.word != null && <span className="snap-word">{drag.word}</span>}
          <span className="snap-time">✂ {drag.value.toFixed(2)}s</span>
        </div>
      )}
    </div>
  );
}

function fmtTick(t: number): string {
  if (t >= 60) {
    const m = Math.floor(t / 60);
    const s = t - m * 60;
    return `${m}:${s < 10 ? "0" : ""}${Number.isInteger(s) ? s : s.toFixed(1)}`;
  }
  return Number.isInteger(t) ? `${t}s` : `${t.toFixed(1)}s`;
}
