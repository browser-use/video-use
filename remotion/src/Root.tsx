import { Composition } from "remotion";
import { EdlComposition } from "./EdlComposition";

// Import the EDL from the standard edit output directory.
// eslint-disable-next-line @typescript-eslint/no-var-requires
let edl: any;
try {
  edl = require("../../edit/edl.json");
} catch {
  edl = { version: 1, sources: {}, ranges: [] };
}

const FPS = 24;

const totalDurationSec = (edl.ranges || []).reduce(
  (sum: number, r: any) => sum + ((r.end || 0) - (r.start || 0)),
  0,
);
const totalFrames = Math.max(1, Math.ceil(totalDurationSec * FPS));

export const Root: React.FC = () => {
  return (
    <Composition
      id="Preview"
      component={EdlComposition}
      durationInFrames={totalFrames}
      fps={FPS}
      width={1920}
      height={1080}
      defaultProps={{ edl }}
    />
  );
};
