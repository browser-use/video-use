interface Props {
  name: string | null;
  syncPulse: number;
  canUndo: boolean;
  canRedo: boolean;
  onUndo(): void;
  onRedo(): void;
  hasNotes: boolean;
  notesOpen: boolean;
  onToggleNotes(): void;
  onExport(preview: boolean): void;
}

export default function TitleBar(p: Props) {
  return (
    <header className="titlebar" data-tauri-drag-region>
      <div className="tb-left" data-tauri-drag-region>
        <span className="tb-name">{p.name ?? "video-use Studio"}</span>
        {p.name && (
          // key remount re-triggers the pulse animation on each agent sync
          <span className="tb-sync" key={p.syncPulse}>
            <span className={"tb-sync-dot" + (p.syncPulse > 0 ? " pulse" : "")} />
            agent synced
          </span>
        )}
      </div>
      {p.name && (
        <div className="tb-right">
          <button className="btn btn-ghost" disabled={!p.canUndo} onClick={p.onUndo} title="Undo (⌘Z)">
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none">
              <path d="M6 3.5 3 6.5l3 3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
              <path d="M3 6.5h6a4 4 0 0 1 0 8H7" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            </svg>
          </button>
          <button className="btn btn-ghost" disabled={!p.canRedo} onClick={p.onRedo} title="Redo (⇧⌘Z)">
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none">
              <path d="M10 3.5l3 3-3 3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
              <path d="M13 6.5H7a4 4 0 0 0 0 8h2" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            </svg>
          </button>
          {p.hasNotes && (
            <button
              className={"btn btn-ghost" + (p.notesOpen ? " active" : "")}
              onClick={p.onToggleNotes}
              title="Project notes (project.md)"
            >
              Notes
            </button>
          )}
          <button
            className="btn btn-primary"
            onClick={(e) => p.onExport(e.altKey)}
            title="Export final.mp4 (⌘E) · ⌥-click for preview render"
          >
            Export
            <span className="kbd">⌘E</span>
          </button>
        </div>
      )}
    </header>
  );
}
