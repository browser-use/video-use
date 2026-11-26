// B-roll kit for seek renderers: every draw is a pure function of t, so headless Chrome can render any frame.
// Load vendor/rough.js first, then this file. Works on a 2D canvas context `g` in logical units.
//
//   BK.camera(g, t, keys)            ease-in-out push/pan between keyframes {t, x, y, z}; call inside save/restore
//   BK.frame(g, img, crop, box, o)   a captured screen in a padded window: title bar, 12px radius, hairline border
//   BK.typeLines(g, img, lines, ...) reveal a capture's real text line by line (lines from capture.js layout.json)
//   BK.cursor(g, t, path)            pointer that glides between points and rings on click
//   BK.spotlight(g, rect, a, W, H)   dim everything but one region
//   BK.marker(g, rect, p)            highlighter sweep behind a line, left to right
//   BK.drift(g, t, t0, cx, cy)       slow push-in on a held frame (about 0.8% a second)
//   BK.flow(g, t, p0, mid, p2)       dots travelling a path, an ambient loop on a held diagram
//   BK.enter(g, t, t0, draw)         blur-in entrance (transparent and blurred to sharp in 0.3 s)
//   BK.popout(g, img, crop, from, to, k, W, H)  lift a capture region out, enlarged, and dim the rest
//   ?texts=1 in the page URL         log drawn strings for texts.js and broll_lint.py
//   BK.sketch(seed)                  rough.js shapes with a fixed seed, drawn stroke by stroke with BK.ink(g, shape, p)
(function () {
  const clamp = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x));
  const ease = p => 1 - Math.pow(1 - clamp(p), 3);
  const easeIO = p => { p = clamp(p); return p < .5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2; };
  const r = (t, a, b) => ease((t - a) / ((b ?? a + .35) - a));
  const lerp = (a, b, p) => a + (b - a) * p;

  function camera(g, t, keys, W, H) {
    let k0 = keys[0], k1 = keys[0];
    for (let i = 0; i < keys.length; i++) { if (t >= keys[i].t) { k0 = keys[i]; k1 = keys[i + 1] || keys[i]; } }
    const d = k1.dur ?? Math.min(1.2, Math.max(.01, k1.t - k0.t));
    const p = k1 === k0 ? 1 : easeIO((t - (k1.t - d)) / d);
    const x = lerp(k0.x, k1.x, p), y = lerp(k0.y, k1.y, p), z = lerp(k0.z, k1.z, p);
    g.translate(W / 2, H / 2); g.scale(z, z); g.translate(-x, -y);
    return { x, y, z };
  }

  // crop: [sx, sy, sw, sh] in image pixels. box: [x, y, w, h] where the screen content lands.
  // Slow push-in on a held frame, content layer only (Nick Saraev: 0.4 to 1.1% per second, so a still never looks frozen).
  function drift(g, t, t0, cx, cy, rate = .008) {
    const s = 1 + rate * Math.max(0, t - t0); g.translate(cx, cy); g.scale(s, s); g.translate(-cx, -cy);
  }

  // Ambient loop: dots travelling a quadratic path through p0, mid, p2, so a held diagram keeps a pulse.
  function flow(g, t, p0, mid, p2, o = {}) {
    const c = [2 * mid[0] - (p0[0] + p2[0]) / 2, 2 * mid[1] - (p0[1] + p2[1]) / 2], n = o.n ?? 3, per = o.period ?? 1.6;
    g.save(); g.globalAlpha *= o.a ?? 1; g.fillStyle = o.color || '#FF5A1F';
    for (let i = 0; i < n; i++) {
      const u = (((t / per) + i / n) % 1 + 1) % 1, v = 1 - u;
      const x = v * v * p0[0] + 2 * v * u * c[0] + u * u * p2[0], y = v * v * p0[1] + 2 * v * u * c[1] + u * u * p2[1];
      g.globalAlpha = (o.a ?? 1) * Math.sin(u * Math.PI); g.beginPath(); g.arc(x, y, o.r ?? 6, 0, 7); g.fill();
    }
    g.restore();
  }

  function frame(g, img, crop, box, o = {}) {
    const [x, y, w, h] = box, bar = o.bar ?? 34, pad = o.pad ?? 0, R = o.radius ?? 12;
    const X = x - pad, Y = y - bar - pad, Wd = w + pad * 2, Hd = h + bar + pad * 2;
    g.save(); g.globalAlpha *= o.a ?? 1;
    g.beginPath(); g.roundRect(X, Y, Wd, Hd, R); g.fillStyle = o.chrome || '#1A1917'; g.fill();
    g.save(); g.clip();
    if (img) g.drawImage(img, crop[0], crop[1], crop[2], crop[3], x, y, w, h);
    g.restore();
    g.lineWidth = 1.5; g.strokeStyle = o.border || 'rgba(255,255,255,.14)'; g.stroke();
    ['#FF5F57', '#FEBC2E', '#28C840'].forEach((c, i) => { g.beginPath(); g.arc(X + 22 + i * 20, Y + bar / 2, 6, 0, 7); g.fillStyle = o.dots === false ? '#55524C' : c; g.fill(); });
    if (o.title) { g.font = `600 15px ${o.font || 'I'}`; g.fillStyle = 'rgba(233,230,220,.7)'; g.textAlign = 'center'; g.textBaseline = 'middle'; g.fillText(o.title, X + Wd / 2, Y + bar / 2); }
    g.restore();
  }

  // Paint the capture's background over every line not yet typed. progress 0..1 across all lines,
  // weighted by line width so long lines take longer. map(x, y) turns capture CSS px into canvas units.
  function typeLines(g, lines, map, sc, progress, bg) {
    const total = lines.reduce((s, l) => s + l.w, 0); let done = total * clamp(progress);
    g.save(); g.fillStyle = bg;
    for (const l of lines) {
      const shown = clamp(done / l.w); done -= l.w;
      if (shown >= 1) continue;
      const a = map(l.x + l.w * shown, l.y - 3), b = map(l.x + l.w + 4, l.y + l.h + 5);
      g.fillStyle = l.bg || bg; g.fillRect(a[0], a[1], b[0] - a[0], b[1] - a[1]);
      if (shown > 0) { g.fillStyle = '#D97757'; g.fillRect(a[0], a[1], 2.5 * sc, b[1] - a[1]); }
    }
    g.restore();
  }

  function cursor(g, t, path, o = {}) {
    if (t < path[0].t) return;
    let i = 0; while (i < path.length - 1 && t >= path[i + 1].t) i++;
    const a = path[i], b = path[i + 1] || a, d = b.glide ?? .7;
    const p = b === a ? 1 : easeIO((t - (b.t - d)) / d);
    const x = lerp(a.x, b.x, p), y = lerp(a.y, b.y, p), s = o.size || 22;
    for (const q of path) if (q.click && t >= q.t && t < q.t + .6) {
      const k = (t - q.t) / .6; g.save(); g.globalAlpha = 1 - k; g.strokeStyle = o.ring || '#FF5A1F'; g.lineWidth = 3;
      g.beginPath(); g.arc(q.x, q.y, 8 + 26 * ease(k), 0, 7); g.stroke(); g.restore();
    }
    g.save(); g.translate(x, y); g.scale(s / 22, s / 22);
    g.beginPath(); g.moveTo(0, 0); g.lineTo(0, 20); g.lineTo(5, 15.5); g.lineTo(8.6, 23); g.lineTo(11.6, 21.6); g.lineTo(8.1, 14.4); g.lineTo(14.6, 14.2); g.closePath();
    g.fillStyle = '#fff'; g.fill(); g.lineWidth = 1.6; g.strokeStyle = '#090519'; g.stroke(); g.restore();
  }

  function spotlight(g, rect, a, W, H, o = {}) {
    if (a <= 0) return; const [x, y, w, h] = rect, R = o.radius ?? 8;
    g.save(); g.globalAlpha = a * (o.dim ?? .62); g.beginPath(); g.rect(0, 0, W, H); g.roundRect(x, y, w, h, R); g.fillStyle = o.color || '#090519'; g.fill('evenodd'); g.restore();
    g.save(); g.globalAlpha = a; g.lineWidth = o.lw ?? 3; g.strokeStyle = o.line || '#FF5A1F'; g.beginPath(); g.roundRect(x, y, w, h, R); g.stroke(); g.restore();
  }

  // Blur-in, the house entrance seen in RoboNuggets: transparent and blurred to sharp in about 0.3 s.
  // Wrap the draw: BK.enter(g, t, t0, () => draw(), {dur, blur, rise}).
  function enter(g, t, t0, draw, o = {}) {
    const k = ease((t - t0) / (o.dur ?? .3)); if (k <= 0) return;
    g.save(); g.globalAlpha *= k; if (k < 1) g.filter = `blur(${((1 - k) * (o.blur ?? 12)).toFixed(2)}px)`;
    g.translate(0, (1 - k) * (o.rise ?? 0)); draw(); g.restore();
  }

  // Pop-out: lift a region of a capture out, enlarged, while the rest dims (about 55% black, RoboNuggets' main device).
  // crop: source rect in image px. from: where that region sits on screen now. to: where it lands, enlarged.
  function popout(g, img, crop, from, to, k, W, H, o = {}) {
    if (k <= 0) return; const e = ease(k), r = [0, 1, 2, 3].map(i => lerp(from[i], to[i], e));
    g.save(); g.globalAlpha = e * (o.dim ?? .55); g.fillStyle = o.shade || '#090519'; g.fillRect(-W, -H, W * 3, H * 3); g.restore();
    g.save(); if (e < 1) g.filter = `blur(${((1 - e) * 8).toFixed(2)}px)`;
    g.beginPath(); g.roundRect(r[0] - 14, r[1] - 12, r[2] + 28, r[3] + 24, 10); g.fillStyle = o.bg || '#1F1E1C'; g.fill();
    g.lineWidth = 2; g.strokeStyle = o.line || '#FF5A1F'; g.stroke();
    g.drawImage(img, crop[0], crop[1], crop[2], crop[3], r[0], r[1], r[2], r[3]); g.restore();
    return r;
  }

  function marker(g, rect, p, o = {}) {
    if (p <= 0) return; const [x, y, w, h] = rect;
    g.save(); g.globalAlpha = o.a ?? .38; g.fillStyle = o.color || '#FF5A1F'; g.fillRect(x, y, w * ease(p), h); g.restore();
  }

  // Rough shapes: build once with a fixed seed (rough.js otherwise re-randomizes every frame and the line boils).
  const lenCache = new WeakMap();
  function pathLen(d) {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'path'); svg.setAttribute('d', d); return svg.getTotalLength();
  }
  function sketch(seed) {
    const gen = rough.generator();
    const base = { seed, roughness: 1.3, bowing: 1.1, strokeWidth: 3.2, stroke: '#0D1C37', hachureGap: 9, fillWeight: 2.2, disableMultiStroke: false };
    const wrap = d => { const paths = gen.toPaths(d).map(p => ({ ...p, path: new Path2D(p.d), len: pathLen(p.d) })); return { paths }; };
    return {
      rect: (x, y, w, h, o = {}) => wrap(gen.rectangle(x, y, w, h, { ...base, ...o })),
      line: (x1, y1, x2, y2, o = {}) => wrap(gen.line(x1, y1, x2, y2, { ...base, ...o })),
      ellipse: (x, y, w, h, o = {}) => wrap(gen.ellipse(x, y, w, h, { ...base, ...o })),
      path: (d, o = {}) => wrap(gen.path(d, { ...base, ...o })),
      poly: (pts, o = {}) => wrap(gen.polygon(pts, { ...base, ...o })),
      curve: (pts, o = {}) => wrap(gen.curve(pts, { ...base, ...o })),
    };
  }
  // Draw a sketch shape p (0..1) of the way through, strokes in order, like a pen.
  function ink(g, shape, p, o = {}) {
    if (p <= 0) return; const total = shape.paths.reduce((s, q) => s + q.len, 0); let left = total * clamp(p);
    g.save(); g.globalAlpha *= o.a ?? 1; g.lineCap = 'round'; g.lineJoin = 'round';
    for (const q of shape.paths) {
      if (left <= 0) break; const show = Math.min(left, q.len); left -= q.len;
      g.strokeStyle = o.stroke || q.stroke; g.lineWidth = q.strokeWidth;
      g.setLineDash(show >= q.len ? [] : [show, q.len + 1]); g.stroke(q.path);
    }
    g.setLineDash([]); g.restore();
  }

  // ?texts=1 logs every string drawn, so texts.js can hand the lint exactly what reached the screen
  if (/[?&]texts=1/.test(location.search)) {
    window.__texts = new Map(); const fill = CanvasRenderingContext2D.prototype.fillText;
    CanvasRenderingContext2D.prototype.fillText = function (s, x, y, ...rest) {
      s = String(s);
      if (this.globalAlpha > .05 && s.trim()) {
        // the string's box on screen, in canvas pixels (texts.js renders at 1920x1080)
        const m = this.measureText(s), w = m.width, al = this.textAlign, x0 = al === 'center' ? x - w / 2 : al === 'right' || al === 'end' ? x - w : x;
        const T = this.getTransform(), pts = [[x0, y - m.actualBoundingBoxAscent], [x0 + w, y + m.actualBoundingBoxDescent]].map(([px, py]) => [T.a * px + T.c * py + T.e, T.b * px + T.d * py + T.f]);
        const box = [Math.min(pts[0][0], pts[1][0]), Math.min(pts[0][1], pts[1][1]), Math.max(pts[0][0], pts[1][0]), Math.max(pts[0][1], pts[1][1])].map(Math.round);
        if (!window.__texts.has(s)) window.__texts.set(s, box);
      }
      return fill.call(this, s, x, y, ...rest);
    };
  }

  window.BK = { clamp, ease, easeIO, r, lerp, camera, frame, typeLines, cursor, spotlight, marker, enter, popout, drift, flow, sketch, ink };
})();
