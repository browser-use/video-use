import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import type { Edl, EditLogEntry, Transcript, Word } from "./types";
import * as tauri from "./lib/tauri";
import {
  appendEditLog,
  getRecents,
  loadProject,
  logEntry,
  projectName,
  pushRecent,
  saveEdl,
  serializeEdl,
  watchEdl,
  withTotal,
} from "./lib/project";
import { segmentOffsets } from "./lib/virtualTime";
import { initial, reducer } from "./appState";
import TitleBar from "./components/TitleBar";
import Preview from "./components/Preview";
import Transport from "./components/Transport";
import Timeline from "./components/Timeline";
import Inspector from "./components/Inspector";
import ExportOverlay from "./components/ExportOverlay";
import EmptyState from "./components/EmptyState";
import "./components/overlays.css";

export default function App() {
  const [state, dispatch] = useReducer(reducer, initial);
  const [booted, setBooted] = useState(!tauri.isTauri);
  const [exporting, setExporting] = useState<{ preview: boolean } | null>(null);
  const [notesOpen, setNotesOpen] = useState(false);
  const stateRef = useRef(state);
  stateRef.current = state;
  const exportingRef = useRef(exporting);
  exportingRef.current = exporting;
  const draggingRef = useRef(false); // uncommitted drag in flight — external changes must not clobber it
  const lastWrittenRef = useRef<string | null>(null); // exact bytes we last wrote/read; filters self-write echoes

  useEffect(() => {
    void tauri.tauriReady().then(() => setBooted(true));
  }, []);

  const persist = useCallback(async (edl: Edl, entries: EditLogEntry[]) => {
    const s = stateRef.current;
    if (!s.edlPath) return;
    lastWrittenRef.current = serializeEdl(edl);
    try {
      await saveEdl(s.edlPath, edl);
      await appendEditLog(s.dir, entries);
    } catch (e) {
      dispatch({ type: "toast", msg: `Save failed: ${e}` });
    }
  }, []);

  const commit = useCallback(
    (edl: Edl, entries: EditLogEntry[]) => {
      const full = withTotal(edl);
      dispatch({ type: "commit", edl: full });
      void persist(full, entries);
    },
    [persist],
  );

  const doUndo = useCallback(() => {
    const stack = stateRef.current.undoStack;
    const prev = stack[stack.length - 1];
    if (!prev) return;
    dispatch({ type: "undo" });
    void persist(prev, [logEntry("undo")]);
  }, [persist]);

  const doRedo = useCallback(() => {
    const stack = stateRef.current.redoStack;
    const next = stack[stack.length - 1];
    if (!next) return;
    dispatch({ type: "redo" });
    void persist(next, [logEntry("redo")]);
  }, [persist]);

  const deleteSegment = useCallback(
    (i: number) => {
      const s = stateRef.current;
      const r = s.edl?.ranges[i];
      if (!s.edl || !r) return;
      dispatch({ type: "select", i: null });
      commit({ ...s.edl, ranges: s.edl.ranges.filter((_, j) => j !== i) }, [
        logEntry("delete", {
          segment: i,
          range: { source: r.source, start: r.start, end: r.end, ...(r.beat ? { beat: r.beat } : {}) },
        }),
      ]);
    },
    [commit],
  );

  const changeGrade = useCallback(
    (preset: string) => {
      const s = stateRef.current;
      if (!s.edl || (s.edl.grade ?? "none") === preset) return;
      commit({ ...s.edl, grade: preset }, [
        logEntry("grade", { before: s.edl.grade ?? "none", after: preset }),
      ]);
    },
    [commit],
  );

  const openProject = useCallback(async (path: string) => {
    try {
      const project = await loadProject(path);
      lastWrittenRef.current = project.raw;
      pushRecent(path);
      dispatch({ type: "open", project });
    } catch (e) {
      dispatch({ type: "toast", msg: `Could not open project: ${e}` });
    }
  }, []);

  const openDialog = useCallback(async () => {
    const path = await tauri.openEdlDialog();
    if (path) await openProject(path);
  }, [openProject]);

  // Browser-mode sample loader (vite dev serves files under the project root).
  const openSample = useCallback(async () => {
    try {
      const raw = await (await fetch("/sample-project/edit/edl.json")).text();
      const edl = JSON.parse(raw) as Edl;
      const transcripts: Record<string, Word[]> = {};
      for (const key of Object.keys(edl.sources ?? {})) {
        const res = await fetch(`/sample-project/edit/transcripts/${key}.json`).catch(() => null);
        if (res?.ok) transcripts[key] = ((await res.json()) as Transcript).words;
      }
      lastWrittenRef.current = raw;
      dispatch({
        type: "open",
        project: {
          edlPath: "/sample-project/edit/edl.json",
          dir: "/sample-project/edit",
          edl,
          raw,
          transcripts,
          projectMd: null,
        },
      });
    } catch (e) {
      dispatch({ type: "toast", msg: `Sample load failed: ${e}` });
    }
  }, []);

  const reloadFromDisk = useCallback(async () => {
    const s = stateRef.current;
    if (!s.edlPath) return;
    try {
      const raw = await tauri.readTextFile(s.edlPath);
      lastWrittenRef.current = raw;
      dispatch({ type: "external", edl: JSON.parse(raw) as Edl });
    } catch (e) {
      dispatch({ type: "toast", msg: `Reload failed: ${e}` });
    }
  }, []);

  // Agent sync: watch edl.json. Self-writes are filtered by exact-content comparison
  // (immune to mtime races); external edits arriving mid-drag queue behind a toast.
  useEffect(() => {
    if (!state.edlPath || !tauri.isTauri) return;
    let unwatch: (() => void) | undefined;
    let closed = false;
    void watchEdl(state.edlPath, () => {
      void (async () => {
        const s = stateRef.current;
        if (!s.edlPath) return;
        try {
          const raw = await tauri.readTextFile(s.edlPath);
          if (raw === lastWrittenRef.current) return;
          const edl = JSON.parse(raw) as Edl; // throws on a partial write → next debounced event retries
          if (draggingRef.current) {
            dispatch({ type: "external-pending" });
          } else {
            lastWrittenRef.current = raw;
            dispatch({ type: "external", edl });
          }
        } catch {
          // unreadable / mid-write — ignore, watcher fires again
        }
      })();
    }).then((u) => {
      if (closed) u();
      else unwatch = u;
    });
    return () => {
      closed = true;
      unwatch?.();
    };
  }, [state.edlPath]);

  // Native file drop opens a project.
  useEffect(() => {
    let un: (() => void) | undefined;
    void tauri
      .onFileDrop((paths) => {
        const p = paths.find((x) => x.endsWith(".json"));
        if (p) void openProject(p);
      })
      .then((u) => {
        un = u;
      });
    return () => un?.();
  }, [openProject]);

  // Output clock: rAF advances the playhead in wall-clock time while playing.
  useEffect(() => {
    if (!state.playing) return;
    let raf = 0;
    let last = performance.now();
    const loop = (now: number) => {
      dispatch({ type: "tick", dt: (now - last) / 1000 });
      last = now;
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, [state.playing]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable) return;
      const s = stateRef.current;
      const mod = e.metaKey || e.ctrlKey;
      if (mod && e.key.toLowerCase() === "z") {
        e.preventDefault();
        if (e.shiftKey) doRedo();
        else doUndo();
      } else if (mod && e.key.toLowerCase() === "e") {
        e.preventDefault();
        if (s.edl) setExporting({ preview: e.altKey });
      } else if (e.code === "Space") {
        e.preventDefault();
        dispatch({ type: "toggle-play" });
      } else if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
        e.preventDefault();
        dispatch({ type: "seek", t: s.playhead + (e.key === "ArrowLeft" ? -1 : 1) / 30 });
      } else if ((e.key === "Backspace" || e.key === "Delete") && s.selection != null) {
        e.preventDefault();
        deleteSegment(s.selection);
      } else if (e.key === "Escape") {
        if (exportingRef.current) setExporting(null);
        else dispatch({ type: "select", i: null });
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [doUndo, doRedo, deleteSegment]);

  // Error toasts auto-dismiss.
  useEffect(() => {
    if (!state.toast) return;
    const id = setTimeout(() => dispatch({ type: "toast", msg: null }), 4500);
    return () => clearTimeout(id);
  }, [state.toast]);

  const { edl } = state;
  const offsets = edl ? segmentOffsets(edl.ranges) : [0];

  return (
    <div className="app">
      <TitleBar
        name={edl ? projectName(state.dir) : null}
        syncPulse={state.syncPulse}
        canUndo={state.undoStack.length > 0}
        canRedo={state.redoStack.length > 0}
        onUndo={doUndo}
        onRedo={doRedo}
        hasNotes={state.projectMd != null}
        notesOpen={notesOpen}
        onToggleNotes={() => setNotesOpen((v) => !v)}
        onExport={(preview) => setExporting({ preview })}
      />
      {!edl ? (
        booted ? (
          <EmptyState
            recents={getRecents()}
            onOpenDialog={() => void openDialog()}
            onOpenPath={(p) => void openProject(p)}
            onOpenSample={() => void openSample()}
          />
        ) : (
          <div className="empty-wrap" />
        )
      ) : (
        <>
          <div className="main">
            <div className="stage">
              <Preview edl={edl} playhead={state.playhead} playing={state.playing} />
              <Transport
                playhead={state.playhead}
                total={offsets[offsets.length - 1]}
                offsets={offsets}
                playing={state.playing}
                onToggle={() => dispatch({ type: "toggle-play" })}
                onSeek={(t) => dispatch({ type: "seek", t })}
              />
            </div>
            <Inspector
              edl={edl}
              selection={state.selection}
              onSelect={(i) => dispatch({ type: "select", i })}
              onDelete={deleteSegment}
              onGrade={changeGrade}
            />
          </div>
          <Timeline
            edl={edl}
            transcripts={state.transcripts}
            playhead={state.playhead}
            selection={state.selection}
            onSeek={(t) => dispatch({ type: "seek", t })}
            onSelect={(i) => dispatch({ type: "select", i })}
            onCommit={commit}
            onDragging={(v) => {
              draggingRef.current = v;
            }}
          />
        </>
      )}
      {notesOpen && state.projectMd != null && (
        <aside className="notes-drawer">
          <div className="insp-head">
            <h2>project.md</h2>
            <button className="btn btn-ghost" onClick={() => setNotesOpen(false)}>
              ✕
            </button>
          </div>
          <pre>{state.projectMd}</pre>
        </aside>
      )}
      {exporting && edl && state.edlPath && (
        <ExportOverlay
          edlPath={state.edlPath}
          dir={state.dir}
          preview={exporting.preview}
          onClose={() => setExporting(null)}
        />
      )}
      {state.externalPending && (
        <div className="toast">
          <span>Agent updated the cut — reload?</span>
          <button className="btn btn-primary" onClick={() => void reloadFromDisk()}>
            Reload
          </button>
          <button className="btn btn-ghost" onClick={() => dispatch({ type: "clear-pending" })}>
            ✕
          </button>
        </div>
      )}
      {state.toast && <div className="toast toast-error">{state.toast}</div>}
    </div>
  );
}
