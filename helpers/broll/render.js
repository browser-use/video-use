// Seek-and-screenshot renderer: headless Chrome over its DevTools socket, frames piped to ffmpeg.
// node render.js <w> <h> <fps> <out.mp4> [t0] [t1]   or   node render.js <w> <h> still <t,t,t> <prefix>
// Renders ./scenes.html (or $SCENES). The page exposes window.ready, window.DUR and window.seek(ct).
// Shared by every episode since November 26, 2026; episodes stop carrying their own copy.
const { spawn } = require('child_process'), fs = require('fs'), path = require('path'), os = require('os');
const [w, h, mode, a, b, c] = process.argv.slice(2);
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'broll-'));
const port = 9300 + (process.pid % 400);
const chrome = spawn(CHROME, ['--headless=new', `--remote-debugging-port=${port}`, `--user-data-dir=${dir}`,
  '--allow-file-access-from-files', '--hide-scrollbars', '--force-device-scale-factor=1', 'about:blank'], { stdio: 'ignore' });
const sleep = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  let tab;
  for (let i = 0; i < 60 && !tab; i++) {
    await sleep(250);
    try { tab = (await (await fetch(`http://127.0.0.1:${port}/json`)).json()).find(x => x.type === 'page'); } catch {}
  }
  const ws = new WebSocket(tab.webSocketDebuggerUrl);
  await new Promise(r => ws.addEventListener('open', r));
  let id = 0; const wait = new Map();
  ws.addEventListener('message', m => { const d = JSON.parse(m.data); if (d.id && wait.has(d.id)) { wait.get(d.id)(d); wait.delete(d.id); } });
  const send = (method, params = {}) => new Promise(r => { const i = ++id; wait.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
  const evalJs = e => send('Runtime.evaluate', { expression: e, awaitPromise: true });

  await send('Page.enable');
  await send('Emulation.setDeviceMetricsOverride', { width: +w, height: +h, deviceScaleFactor: 1, mobile: false });
  await send('Page.navigate', { url: 'file://' + path.resolve(process.env.SCENES || 'scenes.html') + `?w=${w}&h=${h}${process.env.RAW?'&raw=1':''}` });
  await sleep(1200);
  const rd = await evalJs('window.ready.then(()=>window.DUR)');
  const dur = rd.result.result.value;

  const shot = async (t, q = 93) => {
    const ex = await evalJs(`seek(${t})`);
    if (ex.result.exceptionDetails) throw new Error(JSON.stringify(ex.result.exceptionDetails).slice(0, 400));
    const s = await send('Page.captureScreenshot', { format: 'jpeg', quality: q, optimizeForSpeed: true });
    return Buffer.from(s.result.data, 'base64');
  };

  if (mode === 'still') {
    for (const t of a.split(',')) fs.writeFileSync(`${b}_${t}.jpg`, await shot(+t, 88));
  } else {
    const fps = +mode, t0 = +(b || 0), t1 = +(c || dur), n = Math.round((t1 - t0) * fps);
    const ff = spawn('ffmpeg', ['-v', 'error', '-y', '-f', 'image2pipe', '-framerate', String(fps), '-c:v', 'mjpeg', '-i', '-',
      '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '17', '-pix_fmt', 'yuv420p', a], { stdio: ['pipe', 'inherit', 'inherit'] });
    const start = Date.now();
    for (let k = 0; k < n; k++) {
      const buf = await shot(t0 + k / fps);
      if (!ff.stdin.write(buf)) await new Promise(r => ff.stdin.once('drain', r));
      if (k % 150 === 0) console.log(`frame ${k}/${n}, ${((Date.now() - start) / 1000).toFixed(0)}s`);
    }
    ff.stdin.end(); await new Promise(r => ff.on('close', r));
    console.log(`done ${n} frames in ${((Date.now() - start) / 1000).toFixed(0)}s`);
  }
  ws.close(); chrome.kill(); process.exit(0);
})().catch(e => { console.error(e); chrome.kill(); process.exit(1); });
