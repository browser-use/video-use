// Sample what a scene page actually draws, for helpers/broll_lint.py.
// node texts.js <scenes.html> <out.json> [step=0.25]
// The page loads broll-kit.js (which logs fillText under ?texts=1) and exposes window.DUR and window.seek(ct).
// If it also exposes window.rawAt(ct), times are on the raw recording clock, the clock the transcript uses.
const { spawn } = require('child_process'), fs = require('fs'), path = require('path'), os = require('os');
const [page, out, step = '0.25'] = process.argv.slice(2);
const CHROME = process.env.CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'texts-'));
const port = 9500 + (process.pid % 200);
const chrome = spawn(CHROME, ['--headless=new', `--remote-debugging-port=${port}`, `--user-data-dir=${dir}`, '--allow-file-access-from-files', 'about:blank'], { stdio: 'ignore' });
const sleep = ms => new Promise(r => setTimeout(r, ms));
(async () => {
  let tab;
  for (let i = 0; i < 60 && !tab; i++) { await sleep(250); try { tab = (await (await fetch(`http://127.0.0.1:${port}/json`)).json()).find(x => x.type === 'page'); } catch {} }
  const ws = new WebSocket(tab.webSocketDebuggerUrl); await new Promise(r => ws.addEventListener('open', r));
  let id = 0; const wait = new Map();
  ws.addEventListener('message', m => { const d = JSON.parse(m.data); if (d.id && wait.has(d.id)) { wait.get(d.id)(d); wait.delete(d.id); } });
  const send = (method, params = {}) => new Promise(r => { const i = ++id; wait.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
  const ev = async e => (await send('Runtime.evaluate', { expression: e, awaitPromise: true, returnByValue: true })).result.result;
  await send('Page.enable');
  await send('Page.navigate', { url: 'file://' + path.resolve(page) + '?w=1920&h=1080&texts=1' });
  await sleep(1200);
  const dur = (await ev('window.ready.then(()=>window.DUR)')).value, frames = [];
  for (let ct = 0; ct < dur; ct += +step) {
    const r = await ev(`(()=>{window.__texts.clear();seek(${ct});return{t:window.rawAt?rawAt(${ct}):${ct},texts:[...window.__texts].map(([s,box])=>({s,box}))}})()`);
    if (r.exceptionDetails || !r.value) throw new Error('seek failed at ' + ct);
    frames.push(r.value);
  }
  fs.writeFileSync(out, JSON.stringify({ page: path.basename(page), step: +step, frames }, null, 1));
  console.log(`${frames.length} samples to ${out}`); ws.close(); chrome.kill(); process.exit(0);
})().catch(e => { console.error(e); chrome.kill(); process.exit(1); });
