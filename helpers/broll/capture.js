// Screenshot a claude-replay page at device scale 2, fully revealed, plus the boxes a B-roll camera needs.
// node capture.js <replay.html> <out_dir> [width=1600] [height=1000]
// Writes out_dir/session.png, out_dir/layout.json and the same as layout.js (window.LAYOUT): {scale, width, height, blocks:[{role,x,y,w,h}], lines:[{block,x,y,w,h,bg}]}
// in CSS pixels (multiply by scale for image pixels). Lines let a scene type the real text in, line by line.
const { spawn } = require('child_process'), fs = require('fs'), path = require('path'), os = require('os');
const [html, out, W = '1600', H = '1000'] = process.argv.slice(2);
const CHROME = process.env.CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'capture-'));
const port = 9700 + (process.pid % 200);
const chrome = spawn(CHROME, ['--headless=new', `--remote-debugging-port=${port}`, `--user-data-dir=${dir}`,
  '--allow-file-access-from-files', '--hide-scrollbars', 'about:blank'], { stdio: 'ignore' });
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
  const ev = async e => (await send('Runtime.evaluate', { expression: e, awaitPromise: true, returnByValue: true })).result.result.value;
  const key = k => send('Input.dispatchKeyEvent', { type: 'keyDown', key: k, code: k, windowsVirtualKeyCode: k === 'ArrowRight' ? 39 : 0 });

  await send('Page.enable');
  await send('Emulation.setDeviceMetricsOverride', { width: +W, height: +H, deviceScaleFactor: 2, mobile: false });
  await send('Page.navigate', { url: 'file://' + path.resolve(html) });
  await sleep(1500);
  // step to the end so every block and word is lit, then strip the player chrome
  for (let i = 0; i < 400; i++) {
    const p = await ev(`document.getElementById('progress-text')?.textContent||''`);
    const m = /(\d+) of (\d+)/.exec(p); if (m && +m[1] >= +m[2] && +m[2] > 0) break;
    await key('ArrowRight'); await sleep(40);
  }
  await ev(`document.querySelectorAll('.paced-segment').forEach(w=>w.classList.add('word-lit'));
    const st=document.createElement('style');st.textContent='.controls,.progress-wrap,#splash,.splash{display:none!important} *{transition:none!important;animation:none!important}';document.head.appendChild(st);true`);
  await sleep(300);
  const layout = await ev(`(()=>{const r=e=>{const b=e.getBoundingClientRect();return{x:b.left+scrollX,y:b.top+scrollY,w:b.width,h:b.height}};
    const blocks=[...document.querySelectorAll('.turn .user-text, .turn .assistant-text, .turn [class*="user-msg"], .turn [class*="assistant"]')].filter(e=>e.getBoundingClientRect().height>4)
      .map(e=>({role:/user/.test(e.className)?'user':'assistant',...r(e)}));
    const lines=[];document.querySelectorAll('.turn .user-text, .turn .assistant-text').forEach((b,bi)=>{const w=document.createTreeWalker(b,NodeFilter.SHOW_TEXT);let n;
      while((n=w.nextNode())){if(!n.textContent.trim())continue;const g=document.createRange();g.selectNodeContents(n);for(const q of g.getClientRects()){if(q.width<2)continue;
        const L=lines.find(l=>l.block===bi&&Math.abs(l.y-(q.top+scrollY))<4);if(L){const x2=Math.max(L.x+L.w,q.right+scrollX);L.x=Math.min(L.x,q.left+scrollX);L.w=x2-L.x;L.h=Math.max(L.h,q.height)}
        else{let e=n.parentElement,bg='rgb(0, 0, 0)';while(e){const c=getComputedStyle(e).backgroundColor;if(c&&c!=='rgba(0, 0, 0, 0)'&&c!=='transparent'){bg=c;break}e=e.parentElement}
          lines.push({block:bi,x:q.left+scrollX,y:q.top+scrollY,w:q.width,h:q.height,bg})}}}});
    lines.sort((a,b)=>a.y-b.y||a.x-b.x);
    return{scale:2,width:document.documentElement.scrollWidth,height:document.documentElement.scrollHeight,blocks,lines}})()`);
  const shot = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true,
    clip: { x: 0, y: 0, width: layout.width, height: layout.height, scale: 1 } });
  fs.mkdirSync(out, { recursive: true });
  fs.writeFileSync(path.join(out, 'session.png'), Buffer.from(shot.result.data, 'base64'));
  fs.writeFileSync(path.join(out, 'layout.json'), JSON.stringify(layout, null, 1));
  fs.writeFileSync(path.join(out, 'layout.js'), 'window.LAYOUT=' + JSON.stringify(layout) + ';\n'); // file:// pages can't fetch JSON
  console.log(`session.png ${layout.width}x${layout.height} css px, ${layout.blocks.length} blocks, ${layout.lines.length} lines`);
  ws.close(); chrome.kill(); process.exit(0);
})().catch(e => { console.error(e); chrome.kill(); process.exit(1); });
