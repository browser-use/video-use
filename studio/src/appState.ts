import type { Edl, Word } from "./types";
import type { LoadedProject } from "./lib/project";
import { clamp, totalDuration } from "./lib/virtualTime";

export interface State {
  edlPath: string | null;
  dir: string;
  edl: Edl | null;
  transcripts: Record<string, Word[]>;
  projectMd: string | null;
  selection: number | null;
  playhead: number;
  playing: boolean;
  undoStack: Edl[];
  redoStack: Edl[];
  syncPulse: number;
  externalPending: boolean;
  toast: string | null;
}

export type Action =
  | { type: "open"; project: LoadedProject }
  | { type: "commit"; edl: Edl }
  | { type: "undo" }
  | { type: "redo" }
  | { type: "select"; i: number | null }
  | { type: "seek"; t: number }
  | { type: "toggle-play" }
  | { type: "tick"; dt: number }
  | { type: "external"; edl: Edl }
  | { type: "external-pending" }
  | { type: "clear-pending" }
  | { type: "toast"; msg: string | null };

export const initial: State = {
  edlPath: null,
  dir: "",
  edl: null,
  transcripts: {},
  projectMd: null,
  selection: null,
  playhead: 0,
  playing: false,
  undoStack: [],
  redoStack: [],
  syncPulse: 0,
  externalPending: false,
  toast: null,
};

/** Clamp selection/playhead/playing so they stay valid after the EDL changes shape. */
function fit(s: State, edl: Edl): Pick<State, "selection" | "playhead" | "playing"> {
  const total = totalDuration(edl.ranges);
  return {
    selection: s.selection != null && s.selection < edl.ranges.length ? s.selection : null,
    playhead: clamp(s.playhead, 0, total),
    playing: s.playing && total > 0,
  };
}

export function reducer(s: State, a: Action): State {
  switch (a.type) {
    case "open":
      return {
        ...initial,
        edlPath: a.project.edlPath,
        dir: a.project.dir,
        edl: a.project.edl,
        transcripts: a.project.transcripts,
        projectMd: a.project.projectMd,
      };
    case "commit":
      if (!s.edl) return s;
      return {
        ...s,
        edl: a.edl,
        undoStack: [...s.undoStack.slice(-99), s.edl],
        redoStack: [],
        ...fit(s, a.edl),
      };
    case "undo": {
      const prev = s.undoStack[s.undoStack.length - 1];
      if (!prev || !s.edl) return s;
      return {
        ...s,
        edl: prev,
        undoStack: s.undoStack.slice(0, -1),
        redoStack: [...s.redoStack, s.edl],
        ...fit(s, prev),
      };
    }
    case "redo": {
      const next = s.redoStack[s.redoStack.length - 1];
      if (!next || !s.edl) return s;
      return {
        ...s,
        edl: next,
        redoStack: s.redoStack.slice(0, -1),
        undoStack: [...s.undoStack, s.edl],
        ...fit(s, next),
      };
    }
    case "select":
      return { ...s, selection: a.i };
    case "seek":
      return { ...s, playhead: clamp(a.t, 0, s.edl ? totalDuration(s.edl.ranges) : 0) };
    case "toggle-play": {
      if (!s.edl || s.edl.ranges.length === 0) return s;
      if (s.playing) return { ...s, playing: false };
      const total = totalDuration(s.edl.ranges);
      // Play at the very end restarts from the top.
      return { ...s, playing: true, playhead: s.playhead >= total - 1e-3 ? 0 : s.playhead };
    }
    case "tick": {
      if (!s.edl || !s.playing) return s;
      const total = totalDuration(s.edl.ranges);
      const t = s.playhead + a.dt;
      return t >= total ? { ...s, playhead: total, playing: false } : { ...s, playhead: t };
    }
    case "external":
      if (!s.edl) return s;
      return { ...s, edl: a.edl, syncPulse: s.syncPulse + 1, externalPending: false, ...fit(s, a.edl) };
    case "external-pending":
      return { ...s, externalPending: true };
    case "clear-pending":
      return { ...s, externalPending: false };
    case "toast":
      return { ...s, toast: a.msg };
  }
}
