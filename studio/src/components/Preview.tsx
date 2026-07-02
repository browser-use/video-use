import { useEffect, useMemo, useRef } from "react";
import type { Edl } from "../types";
import { fileUrl } from "../lib/tauri";
import { outputToSource, segmentOffsets } from "../lib/virtualTime";

interface Props {
  edl: Edl;
  playhead: number;
  playing: boolean;
}

const DRIFT_PLAYING = 0.2; // resync threshold while playing (seek stutters cost more than drift)
const DRIFT_PAUSED = 1 / 60;
const PRESEEK_WINDOW = 0.3; // pre-seek the next segment's element before the boundary

/** One stacked <video> per source; the rAF output-clock lives in App, this maps it to elements. */
export default function Preview({ edl, playhead, playing }: Props) {
  const videosRef = useRef<Record<string, HTMLVideoElement | null>>({});
  const offsets = useMemo(() => segmentOffsets(edl.ranges), [edl.ranges]);
  const pos = outputToSource(edl.ranges, offsets, playhead);
  const activeKey = pos ? edl.ranges[pos.index].source : null;

  // Runs every render: while playing, App ticks playhead each frame, so this
  // doubles as the per-frame drift-correction loop.
  useEffect(() => {
    const videos = videosRef.current;
    for (const [key, video] of Object.entries(videos)) {
      if (!video) continue;
      if (key !== activeKey || !pos) {
        if (!video.paused) video.pause();
        continue;
      }
      const drift = Math.abs(video.currentTime - pos.sourceTime);
      if (playing) {
        if (drift > DRIFT_PLAYING) video.currentTime = pos.sourceTime;
        if (video.paused) video.play().catch(() => {});
      } else {
        if (!video.paused) video.pause();
        if (drift > DRIFT_PAUSED) video.currentTime = pos.sourceTime;
      }
    }
    // Pre-seek the next segment's source for a clean handoff at the boundary.
    if (pos && playing) {
      const next = edl.ranges[pos.index + 1];
      if (next && next.source !== activeKey && offsets[pos.index + 1] - playhead < PRESEEK_WINDOW) {
        const nv = videos[next.source];
        if (nv && Math.abs(nv.currentTime - next.start) > 0.05) nv.currentTime = next.start;
      }
    }
  });

  return (
    <div className="preview-wrap">
      <div className="preview-frame">
        {Object.entries(edl.sources ?? {}).map(([key, path]) => {
          const src = fileUrl(path);
          return (
            <video
              key={key}
              ref={(el) => {
                videosRef.current[key] = el;
              }}
              className={"preview-video" + (key === activeKey ? " visible" : "")}
              src={src || undefined}
              preload="auto"
              playsInline
            />
          );
        })}
        {!activeKey && <div className="preview-empty">No segments</div>}
      </div>
    </div>
  );
}
