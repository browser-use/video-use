// Guarded Tauri wrappers. All plugin modules are loaded dynamically so the app
// also runs under plain `vite dev` in a browser (rendering an empty state)
// instead of crashing on import.

export const isTauri = typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

type FsMod = typeof import("@tauri-apps/plugin-fs");
type DialogMod = typeof import("@tauri-apps/plugin-dialog");
type ShellMod = typeof import("@tauri-apps/plugin-shell");
type CoreMod = typeof import("@tauri-apps/api/core");
type OpenerMod = typeof import("@tauri-apps/plugin-opener");

let fs: FsMod | null = null;
let dialog: DialogMod | null = null;
let shell: ShellMod | null = null;
let core: CoreMod | null = null;
let opener: OpenerMod | null = null;
let ready: Promise<void> | null = null;

export function tauriReady(): Promise<void> {
  if (!isTauri) return Promise.resolve();
  ready ??= Promise.all([
    import("@tauri-apps/plugin-fs"),
    import("@tauri-apps/plugin-dialog"),
    import("@tauri-apps/plugin-shell"),
    import("@tauri-apps/api/core"),
    import("@tauri-apps/plugin-opener"),
  ]).then(([f, d, s, c, o]) => {
    fs = f;
    dialog = d;
    shell = s;
    core = c;
    opener = o;
  });
  return ready;
}

async function fsMod(): Promise<FsMod> {
  await tauriReady();
  if (!fs) throw new Error("File system unavailable in browser mode");
  return fs;
}

export async function readTextFile(path: string): Promise<string> {
  return (await fsMod()).readTextFile(path);
}

export async function writeTextFile(path: string, content: string): Promise<void> {
  return (await fsMod()).writeTextFile(path, content);
}

export async function appendTextFile(path: string, content: string): Promise<void> {
  return (await fsMod()).writeTextFile(path, content, { append: true });
}

export async function renameFile(from: string, to: string): Promise<void> {
  return (await fsMod()).rename(from, to);
}

export async function fileExists(path: string): Promise<boolean> {
  return (await fsMod()).exists(path);
}

export async function readDirNames(path: string): Promise<string[]> {
  const entries = await (await fsMod()).readDir(path);
  return entries.filter((e) => e.isFile).map((e) => e.name);
}

/** Debounced watch; fires on create/modify/remove/rename (not pure access events). */
export async function watchFile(path: string, cb: () => void): Promise<() => void> {
  const m = await fsMod();
  return m.watch(
    path,
    (ev) => {
      const kind = ev.type;
      if (typeof kind === "object" && kind !== null && "access" in kind) return;
      cb();
    },
    { delayMs: 400 },
  );
}

export async function openEdlDialog(): Promise<string | null> {
  await tauriReady();
  if (!dialog) return null;
  const picked = await dialog.open({
    multiple: false,
    title: "Open edl.json",
    filters: [{ name: "EDL", extensions: ["json"] }],
  });
  return typeof picked === "string" ? picked : null;
}

/** convertFileSrc after boot; empty string in browser mode (video stays black). */
export function fileUrl(absPath: string): string {
  return core ? core.convertFileSrc(absPath) : "";
}

export async function revealItem(path: string): Promise<void> {
  await tauriReady();
  if (opener) await opener.revealItemInDir(path);
}

/** Spawn `video-use <args>` streaming stdout/stderr lines; resolves with exit code. */
export async function runVideoUse(
  args: string[],
  onLine: (line: string, isErr: boolean) => void,
): Promise<number> {
  await tauriReady();
  if (!shell) throw new Error("Shell unavailable in browser mode");
  const cmd = shell.Command.create("video-use", args);
  cmd.stdout.on("data", (line: string) => onLine(line, false));
  cmd.stderr.on("data", (line: string) => onLine(line, true));
  return new Promise<number>((resolve, reject) => {
    cmd.on("close", (payload) => resolve(payload.code ?? -1));
    cmd.on("error", (err) => reject(new Error(String(err))));
    cmd.spawn().catch(reject);
  });
}

/** Native file drag-drop onto the window (Tauri only). */
export async function onFileDrop(cb: (paths: string[]) => void): Promise<() => void> {
  if (!isTauri) return () => {};
  const { getCurrentWebview } = await import("@tauri-apps/api/webview");
  return getCurrentWebview().onDragDropEvent((ev) => {
    if (ev.payload.type === "drop") cb(ev.payload.paths);
  });
}
