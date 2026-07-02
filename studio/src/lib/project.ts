import type { Edl, EditLogEntry, Transcript, Word } from "../types";
import * as tauri from "./tauri";
import { totalDuration } from "./virtualTime";

// Path helpers (POSIX-style; macOS target).
export function dirname(p: string): string {
  const i = p.lastIndexOf("/");
  return i > 0 ? p.slice(0, i) : "/";
}

export function basename(p: string): string {
  return p.slice(p.lastIndexOf("/") + 1);
}

export function stem(name: string): string {
  const b = basename(name);
  const i = b.lastIndexOf(".");
  return i > 0 ? b.slice(0, i) : b;
}

/** "…/my-video/edit/edl.json" → "my-video" (skip the conventional edit/ dir). */
export function projectName(dir: string): string {
  const b = basename(dir);
  return b === "edit" ? basename(dirname(dir)) || b : b;
}

export interface LoadedProject {
  edlPath: string;
  dir: string;
  edl: Edl;
  raw: string;
  transcripts: Record<string, Word[]>;
  projectMd: string | null;
}

export async function loadProject(edlPath: string): Promise<LoadedProject> {
  const raw = await tauri.readTextFile(edlPath);
  const edl = JSON.parse(raw) as Edl;
  if (!Array.isArray(edl.ranges)) throw new Error("edl.json has no ranges[]");
  const dir = dirname(edlPath);

  // Transcripts are keyed to sources by file stem (transcripts/C0103.json → source "C0103").
  const transcripts: Record<string, Word[]> = {};
  try {
    const tdir = `${dir}/transcripts`;
    if (await tauri.fileExists(tdir)) {
      const byStem: Record<string, Word[]> = {};
      for (const name of await tauri.readDirNames(tdir)) {
        if (!name.endsWith(".json")) continue;
        try {
          const parsed = JSON.parse(await tauri.readTextFile(`${tdir}/${name}`)) as Transcript;
          if (Array.isArray(parsed.words)) byStem[stem(name)] = parsed.words;
        } catch {
          // Malformed transcript: skip; snapping falls back to free drag.
        }
      }
      for (const [key, srcPath] of Object.entries(edl.sources ?? {})) {
        const words = byStem[key] ?? byStem[stem(srcPath)];
        if (words) transcripts[key] = words;
      }
    }
  } catch {
    // transcripts/ unreadable — non-fatal.
  }

  let projectMd: string | null = null;
  try {
    if (await tauri.fileExists(`${dir}/project.md`)) {
      projectMd = await tauri.readTextFile(`${dir}/project.md`);
    }
  } catch {
    // non-fatal
  }

  return { edlPath, dir, edl, raw, transcripts, projectMd };
}

export function serializeEdl(edl: Edl): string {
  return JSON.stringify(edl, null, 2) + "\n";
}

export function withTotal(edl: Edl): Edl {
  return { ...edl, total_duration_s: Math.round(totalDuration(edl.ranges) * 100) / 100 };
}

/** Atomic write: tmp file in the same dir, then rename over edl.json. */
export async function saveEdl(edlPath: string, edl: Edl): Promise<void> {
  if (!tauri.isTauri) return; // browser preview: edits stay in memory
  const json = serializeEdl(edl);
  const tmp = `${dirname(edlPath)}/.edl.json.tmp`;
  try {
    await tauri.writeTextFile(tmp, json);
    await tauri.renameFile(tmp, edlPath);
  } catch {
    await tauri.writeTextFile(edlPath, json);
  }
}

export function logEntry(op: string, fields: Record<string, unknown> = {}): EditLogEntry {
  const ts = new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
  return { ts, op, ...fields };
}

export async function appendEditLog(dir: string, entries: EditLogEntry[]): Promise<void> {
  if (!tauri.isTauri || entries.length === 0) return;
  const lines = entries.map((e) => JSON.stringify(e)).join("\n") + "\n";
  await tauri.appendTextFile(`${dir}/edit_log.jsonl`, lines);
}

export async function watchEdl(edlPath: string, onChange: () => void): Promise<() => void> {
  if (!tauri.isTauri) return () => {};
  return tauri.watchFile(edlPath, onChange);
}

// Recent projects (localStorage).
const RECENTS_KEY = "video-use-studio.recents";

export function getRecents(): string[] {
  try {
    const parsed = JSON.parse(localStorage.getItem(RECENTS_KEY) ?? "[]");
    return Array.isArray(parsed) ? parsed.filter((p) => typeof p === "string") : [];
  } catch {
    return [];
  }
}

export function pushRecent(path: string): void {
  const next = [path, ...getRecents().filter((p) => p !== path)].slice(0, 6);
  try {
    localStorage.setItem(RECENTS_KEY, JSON.stringify(next));
  } catch {
    // storage unavailable — ignore
  }
}
