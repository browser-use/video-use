import { fmtTime } from "../lib/virtualTime";

interface Props {
  playhead: number;
  total: number;
  offsets: number[];
  playing: boolean;
  onToggle(): void;
  onSeek(t: number): void;
}

const EPS = 0.05;

export default function Transport({ playhead, total, offsets, playing, onToggle, onSeek }: Props) {
  const prevCut = () => {
    for (let i = offsets.length - 1; i >= 0; i--) {
      if (offsets[i] < playhead - EPS) return onSeek(offsets[i]);
    }
    onSeek(0);
  };
  const nextCut = () => {
    for (const t of offsets) {
      if (t > playhead + EPS) return onSeek(Math.min(t, total));
    }
    onSeek(total);
  };

  return (
    <div className="transport">
      <button className="btn btn-ghost tp-btn" onClick={prevCut} title="Previous cut">
        <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor">
          <rect x="2.5" y="3" width="1.6" height="10" rx="0.8" />
          <path d="M13 3.8v8.4a.8.8 0 0 1-1.24.66L5.6 8.66a.8.8 0 0 1 0-1.32l6.16-4.2A.8.8 0 0 1 13 3.8Z" />
        </svg>
      </button>
      <button className="btn btn-primary tp-play" onClick={onToggle} title="Play / pause (Space)">
        {playing ? (
          <svg width="15" height="15" viewBox="0 0 16 16" fill="currentColor">
            <rect x="3.5" y="2.5" width="3.2" height="11" rx="1" />
            <rect x="9.3" y="2.5" width="3.2" height="11" rx="1" />
          </svg>
        ) : (
          <svg width="15" height="15" viewBox="0 0 16 16" fill="currentColor">
            <path d="M4.5 3.3v9.4a.9.9 0 0 0 1.38.76l7.28-4.7a.9.9 0 0 0 0-1.52L5.88 2.54a.9.9 0 0 0-1.38.76Z" />
          </svg>
        )}
      </button>
      <button className="btn btn-ghost tp-btn" onClick={nextCut} title="Next cut">
        <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor">
          <rect x="11.9" y="3" width="1.6" height="10" rx="0.8" />
          <path d="M3 3.8v8.4a.8.8 0 0 0 1.24.66l6.16-4.2a.8.8 0 0 0 0-1.32L4.24 3.14A.8.8 0 0 0 3 3.8Z" />
        </svg>
      </button>
      <span className="tp-time">
        {fmtTime(playhead)}
        <span className="tp-total"> / {fmtTime(total)}</span>
      </span>
    </div>
  );
}
