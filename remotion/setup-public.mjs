#!/usr/bin/env node
/**
 * Copy source videos listed in edl.json into remotion/public/ so
 * Remotion Studio can serve them. Run with: npm run setup
 *
 * Reads ../edit/edl.json (the standard edit output directory).
 */
import { readFileSync, copyFileSync, existsSync, mkdirSync } from "fs";
import { basename, resolve, dirname } from "path";
import { fileURLToPath } from "url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const publicDir = resolve(__dirname, "public");
const edlPath = resolve(__dirname, "..", "edit", "edl.json");

if (!existsSync(edlPath)) {
  // Try parent's edit dir (when video-use is a skill and edit/ is next to sources)
  console.error(`No edl.json found at ${edlPath}`);
  console.error("Run transcribe + pack + create edl.json first, then re-run this.");
  process.exit(1);
}

mkdirSync(publicDir, { recursive: true });

const edl = JSON.parse(readFileSync(edlPath, "utf-8"));
const copied = new Set();

for (const [name, absPath] of Object.entries(edl.sources)) {
  const filename = basename(absPath);
  if (copied.has(filename)) continue;

  const dest = resolve(publicDir, filename);
  if (existsSync(dest)) {
    console.log(`  exists: ${filename}`);
  } else if (existsSync(absPath)) {
    copyFileSync(absPath, dest);
    console.log(`  copied: ${filename}`);
  } else {
    console.warn(`  MISSING: ${absPath}`);
  }
  copied.add(filename);
}

console.log(`\n${copied.size} source(s) in public/. Run: npm run studio`);
