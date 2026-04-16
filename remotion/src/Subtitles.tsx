import { useCurrentFrame } from "remotion";
import { useMemo } from "react";

interface Word {
  type: string;
  text?: string;
  start?: number;
  end?: number;
  speaker_id?: string;
}

interface Transcript {
  words: Word[];
}

interface EdlRange {
  source: string;
  start: number;
  end: number;
  beat?: string;
}

interface Segment {
  range: EdlRange;
  startFrame: number;
  durationFrames: number;
  srcPath: string;
}

interface Edl {
  sources: Record<string, string>;
  ranges: EdlRange[];
  [key: string]: unknown;
}

interface SubCue {
  frameStart: number;
  frameEnd: number;
  text: string;
}

// Load transcripts at bundle time.  Remotion bundles these via require().
// We try/catch each because not all sources will have transcripts (e.g. meme clips).
const transcriptCache: Record<string, Transcript> = {};

function tryLoadTranscript(name: string): void {
  try {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    transcriptCache[name] = require(`../../edit/transcripts/${name}.json`);
  } catch {
    // no transcript for this source
  }
}

function getTranscript(name: string): Transcript | undefined {
  if (!(name in transcriptCache)) {
    tryLoadTranscript(name);
  }
  return transcriptCache[name];
}

function wordsInRange(transcript: Transcript, start: number, end: number): Word[] {
  return transcript.words.filter(
    (w) => w.type === "word" && w.start != null && w.end != null && w.end! > start && w.start! < end,
  );
}

function buildCues(edl: Edl, segments: Segment[], fps: number): SubCue[] {
  const cues: SubCue[] = [];

  for (const seg of segments) {
    const transcript = getTranscript(seg.range.source);
    if (!transcript) continue;

    const words = wordsInRange(transcript, seg.range.start, seg.range.end);

    for (let i = 0; i < words.length; i += 2) {
      const chunk = words.slice(i, i + 2);
      if (chunk.length === 0) continue;

      const text = chunk
        .map((w) => (w.text || "").trim())
        .join(" ")
        .toUpperCase();
      if (!text) continue;

      const srcStart = chunk[0].start! - seg.range.start;
      const srcEnd = chunk[chunk.length - 1].end! - seg.range.start;
      cues.push({
        frameStart: seg.startFrame + Math.round(srcStart * fps),
        frameEnd: seg.startFrame + Math.round(srcEnd * fps),
        text,
      });
    }
  }

  return cues;
}

export const Subtitles: React.FC<{
  edl: Edl;
  fps: number;
  segments: Segment[];
}> = ({ edl, fps, segments }) => {
  const frame = useCurrentFrame();
  const cues = useMemo(() => buildCues(edl, segments, fps), [edl, segments, fps]);
  const activeCue = cues.find((c) => frame >= c.frameStart && frame < c.frameEnd);

  if (!activeCue) return null;

  return (
    <div
      style={{
        position: "absolute",
        bottom: 60,
        left: 0,
        right: 0,
        display: "flex",
        justifyContent: "center",
        pointerEvents: "none",
      }}
    >
      <span
        style={{
          fontFamily: "Helvetica, Arial, sans-serif",
          fontSize: 48,
          fontWeight: 700,
          color: "white",
          textShadow:
            "2px 2px 0 #000, -2px 2px 0 #000, 2px -2px 0 #000, -2px -2px 0 #000, 0 2px 0 #000, 0 -2px 0 #000, 2px 0 0 #000, -2px 0 0 #000",
          padding: "4px 16px",
          textAlign: "center",
          letterSpacing: "0.05em",
        }}
      >
        {activeCue.text}
      </span>
    </div>
  );
};
