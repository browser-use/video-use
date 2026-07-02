import type { Range, Word } from "../types";

// Pure math for the virtual cut: output time <-> source time via prefix sums,
// plus word-boundary snapping. All functions are side-effect free and unit-testable.

/** offsets[i] = output time at which segment i begins; offsets[n] = total duration. */
export function segmentOffsets(ranges: Range[]): number[] {
  const offsets = [0];
  for (const r of ranges) {
    offsets.push(offsets[offsets.length - 1] + Math.max(0, r.end - r.start));
  }
  return offsets;
}

export function totalDuration(ranges: Range[]): number {
  let t = 0;
  for (const r of ranges) t += Math.max(0, r.end - r.start);
  return t;
}

/** Index of the segment containing output time t (last segment when t >= total). -1 when empty. */
export function segmentAtOutput(offsets: number[], t: number): number {
  const n = offsets.length - 1;
  if (n <= 0) return -1;
  if (t <= 0) return 0;
  if (t >= offsets[n]) return n - 1;
  // Binary search: largest i with offsets[i] <= t.
  let lo = 0;
  let hi = n - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (offsets[mid] <= t) lo = mid;
    else hi = mid - 1;
  }
  return lo;
}

export interface SourcePos {
  index: number;
  sourceTime: number;
}

/** Map output time to (segment index, time within that segment's source file). */
export function outputToSource(ranges: Range[], offsets: number[], t: number): SourcePos | null {
  const i = segmentAtOutput(offsets, t);
  if (i < 0) return null;
  const r = ranges[i];
  const local = Math.min(Math.max(t - offsets[i], 0), Math.max(r.end - r.start, 0));
  return { index: i, sourceTime: r.start + local };
}

/** Sorted, deduped snap targets: every word start and end (only type "word"). */
export function sourceBoundaries(words: Word[]): number[] {
  const set = new Set<number>();
  for (const w of words) {
    if (w.type && w.type !== "word") continue;
    set.add(w.start);
    set.add(w.end);
  }
  return [...set].sort((a, b) => a - b);
}

/** Nearest boundary to t (binary search); returns t unchanged when there are none. */
export function snapToBoundary(bounds: number[], t: number): number {
  if (bounds.length === 0) return t;
  // lo becomes the first index with bounds[lo] >= t.
  let lo = 0;
  let hi = bounds.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (bounds[mid] < t) lo = mid + 1;
    else hi = mid;
  }
  if (lo > 0 && Math.abs(bounds[lo - 1] - t) <= Math.abs(bounds[lo] - t)) return bounds[lo - 1];
  return bounds[lo];
}

/** The word whose start or end sits at boundary time t — shown in the snap tooltip. */
export function wordAtBoundary(words: Word[], t: number): Word | null {
  const EPS = 1e-6;
  for (const w of words) {
    if (w.type && w.type !== "word") continue;
    if (Math.abs(w.end - t) < EPS || Math.abs(w.start - t) < EPS) return w;
  }
  return null;
}

/** "00:12.4" — minutes:seconds.tenths, output-timeline display format. */
export function fmtTime(s: number): string {
  const clamped = Math.max(0, s);
  const m = Math.floor(clamped / 60);
  const sec = clamped - m * 60;
  const whole = Math.floor(sec);
  const tenth = Math.floor((sec - whole) * 10);
  return `${String(m).padStart(2, "0")}:${String(whole).padStart(2, "0")}.${tenth}`;
}

export function clamp(v: number, lo: number, hi: number): number {
  return Math.min(Math.max(v, lo), hi);
}
