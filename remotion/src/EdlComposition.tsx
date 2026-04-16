import { AbsoluteFill, OffthreadVideo, Sequence, staticFile, useCurrentFrame } from "remotion";
import { Subtitles } from "./Subtitles";

const FPS = 24;

interface EdlRange {
  source: string;
  start: number;
  end: number;
  beat?: string;
  note?: string;
}

interface Edl {
  version: number;
  sources: Record<string, string>;
  ranges: EdlRange[];
  [key: string]: unknown;
}

function sourceToStaticFile(absPath: string): string {
  const filename = absPath.split("/").pop() || absPath;
  return staticFile(filename);
}

export const EdlComposition: React.FC<{ edl: Edl }> = ({ edl }) => {
  const segments: Array<{
    range: EdlRange;
    startFrame: number;
    durationFrames: number;
    srcPath: string;
  }> = [];

  let frameOffset = 0;
  for (const range of edl.ranges) {
    const durationSec = range.end - range.start;
    const durationFrames = Math.ceil(durationSec * FPS);
    const srcPath = edl.sources[range.source];
    segments.push({ range, startFrame: frameOffset, durationFrames, srcPath });
    frameOffset += durationFrames;
  }

  return (
    <AbsoluteFill style={{ backgroundColor: "black" }}>
      {segments.map((seg, i) => (
        <Sequence
          key={i}
          from={seg.startFrame}
          durationInFrames={seg.durationFrames}
          name={`${seg.range.beat || "seg"} — ${seg.range.source.slice(0, 30)}`}
        >
          <AbsoluteFill>
            <OffthreadVideo
              src={sourceToStaticFile(seg.srcPath)}
              startFrom={Math.round(seg.range.start * FPS)}
              style={{ width: "100%", height: "100%", objectFit: "contain" }}
            />
          </AbsoluteFill>
        </Sequence>
      ))}

      <Subtitles edl={edl} fps={FPS} segments={segments} />

      {/* Beat label (top-left) */}
      {segments.map((seg, i) => (
        <Sequence key={`label-${i}`} from={seg.startFrame} durationInFrames={seg.durationFrames}>
          <div
            style={{
              position: "absolute",
              top: 20,
              left: 20,
              padding: "4px 12px",
              background: "rgba(0,0,0,0.6)",
              color: seg.range.beat?.includes("MEME") ? "#ff5a00" : "#fff",
              fontFamily: "monospace",
              fontSize: 16,
              borderRadius: 4,
            }}
          >
            {seg.range.beat}
          </div>
        </Sequence>
      ))}
    </AbsoluteFill>
  );
};
