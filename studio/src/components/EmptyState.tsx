import { isTauri } from "../lib/tauri";
import { basename, dirname } from "../lib/project";

interface Props {
  recents: string[];
  onOpenDialog(): void;
  onOpenPath(path: string): void;
  onOpenSample(): void;
}

export default function EmptyState({ recents, onOpenDialog, onOpenPath, onOpenSample }: Props) {
  return (
    <div className="empty-wrap">
      <div className="empty-card">
        <svg className="empty-icon" width="44" height="44" viewBox="0 0 24 24" fill="none">
          <rect x="2.5" y="5" width="19" height="14" rx="3" stroke="currentColor" strokeWidth="1.5" />
          <path d="M2.5 9h19M7 5v4M12 5v4M17 5v4M7 15h6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
        </svg>
        <h1>video-use Studio</h1>
        <p className="empty-hint">
          {isTauri
            ? "Drop an edl.json anywhere in this window, or open one to start editing the cut."
            : "Browser preview — Tauri APIs are unavailable, so files can't be opened or saved."}
        </p>
        {isTauri ? (
          <button className="btn btn-primary empty-open" onClick={onOpenDialog}>
            Open edl.json…
          </button>
        ) : (
          <button className="btn btn-primary empty-open" onClick={onOpenSample}>
            Load sample project
          </button>
        )}
        {recents.length > 0 && (
          <div className="empty-recents">
            <span className="insp-label">Recent</span>
            {recents.map((p) => (
              <button key={p} className="empty-recent" onClick={() => onOpenPath(p)} title={p}>
                <span className="empty-recent-name">{basename(dirname(dirname(p))) || basename(dirname(p))}</span>
                <span className="empty-recent-path">{p}</span>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
