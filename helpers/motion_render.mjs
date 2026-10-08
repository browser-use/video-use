#!/usr/bin/env node
/**
 * Render original local HTML/CSS/SVG/canvas/WebGL motion at exact frame times.
 *
 * Authoring contract:
 *   window.motionReady = Promise.all([...]); // optional asset initialization
 *   window.seek = async (seconds) => { ... }; // absolute, repeatable, reversible
 *
 * All animation must be driven by seek, including paused CSS/WAAPI/GSAP timelines.
 * Bundle assets locally. This renderer blocks remote requests unless explicitly
 * enabled. It does not record wall-clock browser playback.
 *
 * node helpers/motion_render.mjs scene/index.html -o scene/final.mp4 \
 *   --duration 12 --deps /path/to/dependency-project --chrome /path/to/chrome
 * node helpers/motion_render.mjs scene/index.html -o scene/final.mp4 \
 *   --duration 12 --stills-only --stills 0,2,5,8,11.5 --deps /path/to/deps
 *
 * Dependencies: Node >=22.12, puppeteer-core (or puppeteer), Chrome, ffmpeg, ffprobe.
 * API reference: https://pptr.dev/api/puppeteer.page
 */
import { createHash, randomUUID } from 'node:crypto';
import { spawn, spawnSync } from 'node:child_process';
import { once } from 'node:events';
import { createServer } from 'node:http';
import { createRequire } from 'node:module';
import { constants } from 'node:fs';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const HELP = `Usage: node helpers/motion_render.mjs scene/index.html -o final.mp4 --duration 12
  --width N --height N --fps N   Delivery dimensions and rate (1920x1080, 30)
  --deps DIR                    Project or node_modules containing puppeteer-core
  --chrome PATH                 Chrome executable (or CHROME_PATH)
  --root DIR                    Asset server root (defaults to HTML directory)
  --audio PATH                  Optional soundtrack; padded/trimmed to video duration
  --crf N --preset NAME          H264 settings (16, slow)
  --stills 0,2,5                 Additional exact-time PNGs in artifact directory
  --stills-only                 Validate seeks and produce PNGs without encoding
  --artifact-dir DIR            Fresh directory (default: <output-name>.render-<unique>)
  --poster-time N               Poster time in seconds (defaults to 60% of duration)
  --timeout N                   Readiness/seek timeout in milliseconds (60000)
  --allow-remote                Permit remote assets (reduces reproducibility)
  --overwrite                   Replace an existing completed output
  --help                        Print this help
HTML must expose window.seek(seconds), with optional window.motionReady Promise.`;

export function framePlan(duration, fps) {
  if (!Number.isFinite(duration) || duration <= 0 || !Number.isFinite(fps) || fps <= 0) {
    throw new Error('Duration and fps must be positive finite numbers');
  }
  const count = Math.round(duration * fps);
  if (!Number.isSafeInteger(count) || count < 1 || Math.abs(count - duration * fps) > 1e-6) {
    throw new Error('Duration × fps must be an integer; choose a frame-aligned duration');
  }
  return { count, fps, duration: count / fps, lastTime: (count - 1) / fps };
}

export function parseArgs(argv) {
  const options = { width: 1920, height: 1080, fps: 30, crf: 16, preset: 'slow', timeout: 60000, stills: [] };
  const numeric = new Set(['width', 'height', 'fps', 'duration', 'crf', 'timeout', 'poster-time']);
  const boolean = new Set(['stills-only', 'allow-remote', 'overwrite', 'help']);
  const strings = new Set(['deps', 'chrome', 'root', 'audio', 'preset', 'artifact-dir', 'stills']);
  for (let i = 0; i < argv.length; i++) {
    let arg = argv[i];
    if (arg === '-o') arg = '--output';
    if (!arg.startsWith('-')) {
      if (options.input) throw new Error(`Unexpected argument: ${arg}`);
      options.input = arg;
      continue;
    }
    const key = arg.slice(2);
    if (boolean.has(key)) options[key] = true;
    else if (numeric.has(key) || strings.has(key) || key === 'output') {
      if (i + 1 >= argv.length) throw new Error(`Missing value for ${arg}`);
      const value = argv[++i];
      options[key] = numeric.has(key) ? Number(value) : value;
    } else throw new Error(`Unknown option: ${arg}`);
  }
  if (options.help) return options;
  if (!options.input || !options.output || options.duration === undefined) throw new Error(HELP);
  framePlan(options.duration, options.fps);
  for (const key of ['width', 'height']) {
    if (!Number.isSafeInteger(options[key]) || options[key] < 2 || options[key] % 2) {
      throw new Error(`${key} must be a positive even integer for H264 yuv420p`);
    }
  }
  if (!Number.isFinite(options.timeout) || options.timeout <= 0) throw new Error('timeout must be positive');
  if (!Number.isInteger(options.crf) || options.crf < 0 || options.crf > 51) throw new Error('crf must be 0–51');
  if (!['ultrafast', 'superfast', 'veryfast', 'faster', 'fast', 'medium', 'slow', 'slower', 'veryslow'].includes(options.preset)) {
    throw new Error('Invalid x264 preset');
  }
  if (!options.output.toLowerCase().endsWith('.mp4')) throw new Error('Output must be an .mp4 file');
  if (typeof options.stills === 'string') options.stills = options.stills.split(',').map(Number);
  options['poster-time'] ??= options.duration * 0.6;
  for (const time of [...options.stills, options['poster-time']]) {
    if (!Number.isFinite(time) || time < 0 || time >= options.duration) {
      throw new Error('Still and poster times must be in [0, duration)');
    }
  }
  return options;
}

export function isWithin(root, target) {
  const relative = path.relative(root, target);
  return relative === '' || (!path.isAbsolute(relative) && relative !== '..' && !relative.startsWith(`..${path.sep}`));
}

const sha = bytes => createHash('sha256').update(bytes).digest('hex');
const exists = async filename => { try { await fs.lstat(filename); return true; } catch (error) { if (error.code === 'ENOENT') return false; throw error; } };
const mime = { '.html': 'text/html', '.js': 'text/javascript', '.mjs': 'text/javascript', '.css': 'text/css', '.json': 'application/json', '.svg': 'image/svg+xml', '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp', '.woff': 'font/woff', '.woff2': 'font/woff2', '.ttf': 'font/ttf', '.otf': 'font/otf', '.mp4': 'video/mp4', '.webm': 'video/webm', '.wasm': 'application/wasm' };

// Single byte ranges enable real HTML media seeking. Ignore unknown units and
// multi-range requests; this small local server does not produce multipart bodies.
function singleByteRange(header, length) {
  if (typeof header !== 'string') return null;
  const unit = /^bytes=(.*)$/i.exec(header.trim());
  if (!unit || unit[1].includes(',')) return null;
  const parts = /^(\d*)-(\d*)$/.exec(unit[1]);
  if (!parts || (!parts[1] && !parts[2]) || length === 0) return { unsatisfiable: true };
  const size = BigInt(length);
  if (!parts[1]) {
    const suffix = BigInt(parts[2]);
    if (suffix === 0n) return { unsatisfiable: true };
    return { start: Number(suffix >= size ? 0n : size - suffix), end: length - 1 };
  }
  const start = BigInt(parts[1]);
  const end = parts[2] ? BigInt(parts[2]) : size - 1n;
  if (start >= size || end < start) return { unsatisfiable: true };
  return { start: Number(start), end: Number(end >= size ? size - 1n : end) };
}

export async function startAssetServer(rootDir) {
  const root = await fs.realpath(rootDir);
  const assets = new Map();
  const server = createServer(async (req, res) => {
    try {
      if (!['GET', 'HEAD'].includes(req.method)) { res.writeHead(405).end(); return; }
      const pathname = decodeURIComponent(new URL(req.url, 'http://localhost').pathname);
      if (pathname === '/favicon.ico') { res.writeHead(204).end(); return; }
      const filename = await fs.realpath(path.resolve(root, `.${pathname}`));
      if (!isWithin(root, filename)) { res.writeHead(403).end('Outside asset root'); return; }
      const bytes = await fs.readFile(filename);
      assets.set(path.relative(root, filename), { sha256: sha(bytes), bytes: bytes.length });
      const headers = { 'Content-Type': mime[path.extname(filename).toLowerCase()] || 'application/octet-stream', 'Content-Length': bytes.length, 'Cache-Control': 'no-store', 'Accept-Ranges': 'bytes' };
      // RFC 9110 defines Range for GET only; HEAD describes the full representation.
      const range = req.method === 'GET' ? singleByteRange(req.headers.range, bytes.length) : null;
      if (range?.unsatisfiable) {
        res.writeHead(416, { ...headers, 'Content-Length': 0, 'Content-Range': `bytes */${bytes.length}` }).end();
      } else if (range) {
        res.writeHead(206, { ...headers, 'Content-Length': range.end - range.start + 1, 'Content-Range': `bytes ${range.start}-${range.end}/${bytes.length}` });
        res.end(bytes.subarray(range.start, range.end + 1));
      } else {
        res.writeHead(200, headers);
        res.end(req.method === 'HEAD' ? undefined : bytes);
      }
    } catch (error) {
      res.writeHead(error.code === 'ENOENT' ? 404 : 400).end('Asset unavailable');
    }
  });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  return { server, assets, root, origin: `http://127.0.0.1:${server.address().port}` };
}

async function resolvePuppeteer(deps) {
  const bases = [deps, process.cwd(), path.dirname(fileURLToPath(import.meta.url))].filter(Boolean);
  for (const base of bases) {
    const require = createRequire(path.join(path.resolve(base), '__motion_resolve__.cjs'));
    for (const name of ['puppeteer-core', 'puppeteer']) {
      try { return (await import(pathToFileURL(require.resolve(name)).href)).default; } catch (error) {
        if (error.code !== 'MODULE_NOT_FOUND' && error.code !== 'ERR_MODULE_NOT_FOUND') throw error;
      }
    }
  }
  throw new Error('Missing puppeteer-core. Install it in the scene dependency project and pass --deps DIR.');
}

async function resolveChrome(explicit) {
  if (explicit) { await fs.access(explicit, constants.X_OK); return explicit; }
  const candidates = [
    process.env.CHROME_PATH,
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/Applications/Chromium.app/Contents/MacOS/Chromium',
    '/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser',
  ].filter(Boolean);
  for (const candidate of candidates) if (await exists(candidate)) return candidate;
  throw new Error('Chrome unavailable; set --chrome PATH or CHROME_PATH.');
}

function run(binary, args) {
  const result = spawnSync(binary, args, { encoding: 'utf8', maxBuffer: 16 * 1024 * 1024 });
  if (result.error || result.status !== 0) throw new Error(`${binary} failed: ${result.error?.message || result.stderr}`);
  return result.stdout;
}

async function timed(promise, timeout, label) {
  let timer;
  try {
    return await Promise.race([promise, new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error(`${label} exceeded ${timeout} ms`)), timeout);
    })]);
  } finally { clearTimeout(timer); }
}

export async function closeBrowser(browser, timeout = 5000, warn = console.warn) {
  if (!browser) return;
  const child = browser.process();
  try {
    await timed(browser.close(), timeout, 'Browser shutdown');
  } catch (error) {
    warn(`Renderer cleanup: ${error.message}; releasing its browser process`);
    // This is only the fresh process launched by this render, never a user's
    // existing browser. A crashed/unresponsive renderer must not hang export.
    if (child && child.exitCode === null && child.signalCode === null) child.kill('SIGKILL');
  } finally {
    try { await timed(Promise.resolve(browser.disconnect()), 1000, 'Browser disconnect'); } catch { /* already disconnected */ }
    // Chrome helper processes can inherit stdout/stderr on macOS. Even after
    // Chrome exits, those inherited pipes otherwise keep Node alive indefinitely.
    for (const stream of child?.stdio || []) stream?.destroy?.();
    child?.unref?.();
  }
}

export async function closeAssetServer(server) {
  if (!server) return;
  const closed = new Promise(resolve => server.close(resolve));
  server.closeAllConnections?.();
  await timed(closed, 5000, 'Asset server shutdown');
}

export async function writeEncoderFrame(encoder, completion, bytes, frame) {
  try {
    if (encoder.exitCode !== null || encoder.stdin.destroyed) {
      await completion;
      throw new Error('Encoder input closed unexpectedly');
    }
    // The write callback also handles backpressure, while preserving EPIPE as
    // an encoder failure that can be enriched with ffmpeg's stderr/exit signal.
    await new Promise((resolve, reject) => encoder.stdin.write(bytes, error => error ? reject(error) : resolve()));
  } catch (writeError) {
    let detail = writeError.message;
    try { await timed(completion, 2000, 'Encoder failure report'); }
    catch (encoderError) { detail = `${encoderError.message}\nInput: ${writeError.message}`; }
    throw new Error(`Encoding failed at frame ${frame + 1}: ${detail}`);
  }
}

// Hard-link publication is atomic and refuses even a dangling destination symlink.
// The temporary file is a sibling, so both paths use the same filesystem.
export async function publishOutput(temporary, output, overwrite = false) {
  if (overwrite) await fs.rename(temporary, output);
  else {
    await fs.link(temporary, output);
    await fs.unlink(temporary);
  }
}

export async function render(options) {
  const plan = framePlan(options.duration, options.fps);
  const input = await fs.realpath(path.resolve(options.input));
  const output = path.resolve(options.output);
  let artifactDir;
  if (!options.overwrite && !options['stills-only'] && await exists(output)) throw new Error(`Output exists: ${output}; use --overwrite`);
  if (options.audio) await fs.access(path.resolve(options.audio));
  run('ffmpeg', ['-version']);
  run('ffprobe', ['-version']);
  const puppeteer = await resolvePuppeteer(options.deps);
  const executablePath = await resolveChrome(options.chrome);
  await fs.mkdir(path.dirname(output), { recursive: true });
  if (options['artifact-dir']) {
    artifactDir = path.resolve(options['artifact-dir']);
    await fs.mkdir(path.dirname(artifactDir), { recursive: true });
    await fs.mkdir(artifactDir); // Never reuse a directory containing someone else's files.
  } else artifactDir = await fs.mkdtemp(output.slice(0, -4) + '.render-');
  const serving = await startAssetServer(options.root || path.dirname(input));
  if (!isWithin(serving.root, input)) {
    serving.server.close();
    throw new Error('Input HTML must be inside --root');
  }
  const entry = path.relative(serving.root, input).split(path.sep).map(encodeURIComponent).join('/');
  const tempOutput = path.join(path.dirname(output), `.${path.basename(output)}.${randomUUID()}.rendering.mp4`);
  const errors = [];
  let browser, encoder, encoderDone;
  const started = Date.now();
  const failOnErrors = () => { if (errors.length) throw new Error(`Browser render failed:\n${[...new Set(errors)].join('\n')}`); };
  try {
    browser = await puppeteer.launch({ executablePath, headless: true, args: ['--force-color-profile=srgb', '--hide-scrollbars', '--disable-accelerated-2d-canvas', '--disable-background-timer-throttling', '--disable-renderer-backgrounding', '--autoplay-policy=no-user-gesture-required'], defaultViewport: { width: options.width, height: options.height, deviceScaleFactor: 1 } });
    const page = await browser.newPage();
    page.setDefaultTimeout(options.timeout);
    page.setDefaultNavigationTimeout(options.timeout);
    page.on('pageerror', error => errors.push(error.message));
    page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
    page.on('requestfailed', request => errors.push(`${request.url()}: ${request.failure()?.errorText}`));
    page.on('response', response => { if (response.status() >= 400) errors.push(`HTTP ${response.status()}: ${response.url()}`); });
    await page.setRequestInterception(true);
    page.on('request', request => {
      const url = request.url();
      if (url.startsWith(serving.origin + '/') || /^(data|blob|about):/.test(url) || options['allow-remote']) request.continue();
      else { errors.push(`Remote asset blocked: ${url}. Bundle it locally or use --allow-remote.`); request.abort(); }
    });
    await page.goto(`${serving.origin}/${entry}`, { waitUntil: 'networkidle0' });
    failOnErrors();
    await timed(page.evaluate(async () => {
      await window.motionReady;
      await document.fonts.ready;
      await Promise.all([...document.images].map(image => image.decode()));
      const failedFonts = [...document.fonts].filter(font => font.status === 'error').map(font => font.family);
      if (failedFonts.length) throw new Error(`Failed fonts: ${failedFonts.join(', ')}`);
      if (typeof window.seek !== 'function') throw new Error('HTML must expose window.seek(seconds)');
    }), options.timeout, 'Asset readiness');
    failOnErrors();

    const capture = async (time) => {
      await timed(page.evaluate(async (seconds) => {
        await window.seek(seconds);
        await document.fonts.ready;
        await Promise.all([...document.images].map(image => image.decode()));
        const running = document.getAnimations().filter(animation => animation.playState === 'running');
        if (running.length) throw new Error(`${running.length} CSS/WAAPI animations still running; pause them and set currentTime in seek()`);
        const media = [...document.querySelectorAll('video, audio')].filter(element => !element.paused);
        if (media.length) throw new Error('Media is playing on wall clock; pause and seek it explicitly');
        // Flush layout and let the compositor consume CSS/SVG/WebGL state.
        document.documentElement.getBoundingClientRect();
        await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      }, time), options.timeout, `seek(${time})`);
      const bytes = Buffer.from(await page.screenshot({ type: 'png', captureBeyondViewport: false, optimizeForSpeed: true }));
      failOnErrors();
      return bytes;
    };

    // Ascending, backward, then repeated seeks catch hidden accumulated state.
    const checkTimes = [...new Set([0, Math.floor(plan.count * .31) / plan.fps, Math.floor(plan.count * .73) / plan.fps, plan.lastTime])];
    const hashes = new Map();
    const checkFrames = new Map();
    for (const time of checkTimes) {
      const bytes = await capture(time);
      hashes.set(time, sha(bytes));
      checkFrames.set(time, bytes);
    }
    for (const time of [...checkTimes].reverse()) {
      const bytes = await capture(time);
      const observed = sha(bytes);
      if (observed !== hashes.get(time)) {
        await fs.writeFile(path.join(artifactDir, 'seek-failure-expected.png'), checkFrames.get(time));
        await fs.writeFile(path.join(artifactDir, 'seek-failure-observed.png'), bytes);
        await fs.writeFile(path.join(artifactDir, 'seek-failure.json'), JSON.stringify({ time, expected: hashes.get(time), observed }, null, 2) + '\n');
        throw new Error(`Nondeterministic frame at ${time}s: backward/repeated seek changed pixels. Proof images saved in ${artifactDir}. Eliminate wall-clock motion, mutable random state, and accumulated transforms.`);
      }
    }
    checkFrames.clear();
    console.log(`Seek validation passed at ${checkTimes.length} times (${options.width}×${options.height})`);
    const stills = [];
    const poster = path.join(artifactDir, 'poster.png');
    await fs.writeFile(poster, await capture(options['poster-time']));
    for (const [index, time] of options.stills.entries()) {
      const filename = path.join(artifactDir, `still-${index}-${time.toFixed(4).replace('.', '_')}.png`);
      await fs.writeFile(filename, await capture(time));
      stills.push({ time, path: filename });
    }
    const metadata = {
      schema: 'video-use.motion-render.v1', source: input, sourceSha256: sha(await fs.readFile(input)),
      output: options['stills-only'] ? null : output, width: options.width, height: options.height,
      fps: options.fps, frameCount: plan.count, duration: plan.duration, firstFrameTime: 0, lastFrameTime: plan.lastTime,
      poster: { time: options['poster-time'], path: poster }, stills,
      deterministicChecks: [...hashes].map(([time, sha256]) => ({ time, sha256, backwardSeekMatches: true })),
      browser: await browser.version(), chrome: executablePath, node: process.version,
      remoteAssetsAllowed: Boolean(options['allow-remote']), audio: options.audio ? path.resolve(options.audio) : null,
      encoding: { codec: 'libx264', pixelFormat: 'yuv420p', crf: options.crf, preset: options.preset, faststart: true, colorSpace: 'bt709' },
    };
    if (!options['stills-only']) {
      const args = ['-hide_banner', '-loglevel', 'error', '-y', '-f', 'image2pipe', '-framerate', String(plan.fps), '-vcodec', 'png', '-i', 'pipe:0'];
      if (options.audio) args.push('-i', path.resolve(options.audio), '-map', '0:v:0', '-map', '1:a:0', '-af', `apad,atrim=duration=${plan.duration}`, '-c:a', 'aac', '-b:a', '320k');
      else args.push('-an');
      args.push('-frames:v', String(plan.count), '-c:v', 'libx264', '-crf', String(options.crf), '-preset', options.preset,
        '-vf', 'scale=in_range=full:out_range=tv:out_color_matrix=bt709,format=yuv420p',
        '-color_range', 'tv', '-colorspace', 'bt709', '-color_primaries', 'bt709', '-color_trc', 'bt709', '-movflags', '+faststart', tempOutput);
      encoder = spawn('ffmpeg', args, { stdio: ['pipe', 'ignore', 'pipe'] });
      let encoderError = '';
      encoder.stderr.on('data', bytes => { encoderError = (encoderError + bytes).slice(-16000); });
      // Attach handlers immediately; ffmpeg may fail before the next frame is ready.
      encoderDone = new Promise((resolve, reject) => {
        encoder.once('error', reject);
        encoder.once('close', (code, signal) => code === 0 ? resolve() : reject(new Error(`ffmpeg exited ${signal ? `with signal ${signal}` : `with code ${code}`}: ${encoderError || '(no stderr)'}`)));
      });
      encoderDone.catch(() => {});
      encoder.stdin.on('error', () => {});
      for (let frame = 0; frame < plan.count; frame++) {
        const bytes = await capture(frame / plan.fps);
        await writeEncoderFrame(encoder, encoderDone, bytes, frame);
        if (frame === 0 || (frame + 1) % 30 === 0 || frame + 1 === plan.count) console.log(`Frame ${frame + 1}/${plan.count}`);
      }
      encoder.stdin.end();
      await encoderDone;
      const probe = JSON.parse(run('ffprobe', ['-v', 'error', '-count_frames', '-show_streams', '-show_format', '-of', 'json', tempOutput]));
      const video = probe.streams.find(stream => stream.codec_type === 'video');
      if (!video || Number(video.nb_read_frames) !== plan.count || video.width !== options.width || video.height !== options.height) {
        throw new Error('Encoded video failed frame count or dimension validation');
      }
      metadata.probe = probe;
      metadata.probe.format.filename = output;
      await publishOutput(tempOutput, output, options.overwrite);
    }
    metadata.localAssets = Object.fromEntries([...serving.assets].sort(([a], [b]) => a.localeCompare(b)));
    metadata.elapsedSeconds = (Date.now() - started) / 1000;
    const manifest = path.join(artifactDir, options['stills-only'] ? 'stills.json' : 'render.json');
    await fs.writeFile(manifest, JSON.stringify(metadata, null, 2) + '\n');
    console.log(`Saved ${options['stills-only'] ? artifactDir : output}\nManifest: ${manifest}`);
    return metadata;
  } finally {
    if (encoder && encoder.exitCode === null) encoder.kill('SIGTERM');
    // Release every owned resource even if one cleanup operation fails.
    const cleaned = await Promise.allSettled([
      closeBrowser(browser), closeAssetServer(serving.server), fs.rm(tempOutput, { force: true }),
    ]);
    for (const result of cleaned) if (result.status === 'rejected') console.warn(`Renderer cleanup: ${result.reason.message}`);
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const options = parseArgs(process.argv.slice(2));
    if (options.help) console.log(HELP);
    else await render(options);
  } catch (error) { console.error(error.stack || error.message); process.exitCode = 1; }
}
