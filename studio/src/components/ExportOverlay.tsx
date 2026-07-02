import { useEffect, useRef, useState } from "react";
import { isTauri, revealItem, runVideoUse } from "../lib/tauri";

interface Props {
  edlPath: string;
  dir: string;
  preview: boolean;
  onClose(): void;
}

interface Line {
  text: string;
  err: boolean;
}

type Status = "running" | "done" | "failed";

export default function ExportOverlay({ edlPath, dir, preview, onClose }: Props) {
  const [lines, setLines] = useState<Line[]>([]);
  const [status, setStatus] = useState<Status>("running");
  const logRef = useRef<HTMLDivElement>(null);
  const outPath = `${dir}/${preview ? "preview.mp4" : "final.mp4"}`;

  useEffect(() => {
    let alive = true;
    const push = (text: string, err: boolean) => {
      if (alive && text.trim() !== "") setLines((prev) => [...prev.slice(-500), { text, err }]);
    };
    if (!isTauri) {
      push("Export requires the desktop app (browser preview mode).", true);
      setStatus("failed");
      return;
    }
    const args = ["render", edlPath, "-o", outPath, ...(preview ? ["--preview"] : [])];
    push(`$ video-use ${args.join(" ")}`, false);
    runVideoUse(args, push)
      .then((code) => {
        if (!alive) return;
        if (code === 0) setStatus("done");
        else {
          push(`video-use exited with code ${code}`, true);
          setStatus("failed");
        }
      })
      .catch((e) => {
        if (!alive) return;
        push(`Failed to launch video-use — is it on your PATH? (${e})`, true);
        setStatus("failed");
      });
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [lines]);

  return (
    <div className="overlay-backdrop">
      <div className="export-card">
        <div className="export-head">
          <h2>{preview ? "Rendering preview" : "Exporting final.mp4"}</h2>
          {status === "running" && <span className="export-status running">rendering…</span>}
          {status === "done" && <span className="export-status done">done</span>}
          {status === "failed" && <span className="export-status failed">failed</span>}
        </div>
        {status === "running" && <div className="shimmer" />}
        <div className="export-log" ref={logRef}>
          {lines.map((l, i) => (
            <div key={i} className={l.err ? "log-err" : "log-line"}>
              {l.text}
            </div>
          ))}
        </div>
        <div className="export-foot">
          {status === "done" && (
            <>
              <span className="export-path mono" title={outPath}>
                {outPath}
              </span>
              <button className="btn" onClick={() => void revealItem(outPath)}>
                Reveal
              </button>
            </>
          )}
          <button className="btn btn-ghost" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
