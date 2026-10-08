/** Seekable, style-neutral DOM/SVG components for authored motion design.
 * GSAP is supplied by the composition, never downloaded by this module.
 * All animation helpers add explicit endpoint tweens to a paused timeline.
 */
(function (global) {
  "use strict";
  const SVG = "http://www.w3.org/2000/svg";

  // Reject invalid clocks before building a timeline.
  function seconds(frame, fps = 30) {
    if (!Number.isInteger(frame) || frame < 0 || !Number.isFinite(fps) || fps <= 0) {
      throw new Error("frame must be a nonnegative integer and fps must be positive");
    }
    return frame / fps;
  }

  // Stable procedural variation, independent of render order or wall time.
  function seeded(seed = 1) {
    let state = seed >>> 0;
    return () => {
      state = (Math.imul(1664525, state) + 1013904223) >>> 0;
      return state / 4294967296;
    };
  }

  // Pure normalized spring response for geometry or sampled plots.
  function springAt(t, { damping = 7, frequency = 12 } = {}) {
    if (![t, damping, frequency].every(Number.isFinite) || damping <= 0 || frequency <= 0) {
      throw new Error("spring parameters must be finite, damping/frequency positive");
    }
    return t <= 0 ? 0 : 1 - Math.exp(-damping * t) *
      (Math.cos(frequency * t) + damping / frequency * Math.sin(frequency * t));
  }

  // Create text safely without interpreting supplied copy as HTML.
  function element(tag, className, text, style = {}) {
    const node = document.createElement(tag);
    node.className = className || "";
    if (text !== undefined) node.textContent = String(text);
    Object.assign(node.style, style);
    return node;
  }

  // Create SVG geometry with explicit attributes.
  function vector(tag, attributes = {}) {
    const node = document.createElementNS(SVG, tag);
    for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, String(value));
    return node;
  }

  // Set only supplied tokens; a project owns its palette and type choices.
  function theme(root, tokens) {
    for (const [key, value] of Object.entries(tokens)) {
      if (!/^[a-z][a-z0-9-]*$/.test(key)) throw new Error(`invalid theme token: ${key}`);
      root.style.setProperty(`--motion-${key}`, String(value));
    }
    return root;
  }

  // Build a browser/window shell with individually addressable chrome and content.
  function windowPanel({ title = "", width = 760, height = 420 } = {}) {
    const root = element("section", "motion-window", undefined, { width: `${width}px`, height: `${height}px` });
    const chrome = element("header", "motion-chrome");
    const controls = element("span", "motion-controls");
    for (let i = 0; i < 3; i++) controls.append(element("i", "motion-control"));
    const label = element("span", "motion-window-title", title);
    chrome.append(controls, label);
    const body = element("div", "motion-window-body");
    root.append(chrome, body);
    return { root, chrome, controls, label, body };
  }

  // Terminal lines are authored data, never shell commands to execute.
  function terminal({ title = "Terminal", lines = [], width = 760, height = 420 } = {}) {
    const panel = windowPanel({ title, width, height });
    panel.root.classList.add("motion-terminal");
    const rows = lines.map(line => {
      const spec = typeof line === "string" ? { text: line } : line;
      const row = element("div", "motion-terminal-line", spec.text);
      if (spec.color) row.style.color = spec.color;
      panel.body.append(row);
      return row;
    });
    return { ...panel, rows };
  }

  // A prompt field whose full text layout is stable while characters reveal.
  function promptBar({ text = "", action = "Send", mode = "", width = 920 } = {}) {
    const root = element("div", "motion-prompt", undefined, { width: `${width}px` });
    const plus = element("span", "motion-prompt-plus", "+");
    const input = element("div", "motion-prompt-input", text);
    const modeLabel = element("span", "motion-prompt-mode", mode);
    const button = element("div", "motion-button", action);
    root.append(plus, input, modeLabel, button);
    return { root, plus, input, mode: modeLabel, button };
  }

  // Menu rows can be selected with a separately animated highlight layer.
  function menu({ items = [], width = 320 } = {}) {
    const root = element("div", "motion-menu", undefined, { width: `${width}px` });
    const rows = items.map(text => element("div", "motion-menu-row", text));
    root.append(...rows);
    return { root, rows };
  }

  // A cursor is geometry with a fixed hotspot at its upper-left corner.
  function cursor({ color = "#fff", stroke = "#111", size = 36 } = {}) {
    const root = element("div", "motion-cursor", undefined, { width: `${size}px`, height: `${size}px` });
    const svg = vector("svg", { viewBox: "0 0 32 32", width: "100%", height: "100%" });
    svg.append(vector("path", { d: "M3 2 L4 26 L10 20 L15 30 L20 27 L15 18 L25 17 Z", fill: color, stroke, "stroke-width": 1.5 }));
    root.append(svg);
    return { root, svg };
  }

  // A 2.5D sphere uses layered gradients; real changing light/parallax needs 3D.
  function orb({ size = 180, color = "#315fa0", highlight = "#eef7ff" } = {}) {
    return element("div", "motion-orb", undefined, {
      width: `${size}px`, height: `${size}px`,
      background: `radial-gradient(circle at 32% 23%, ${highlight}, ${color} 39%, #071021 93%)`,
    });
  }

  // Load a local logo as an image; do not inject untrusted SVG markup.
  function logo({ src, width = 120, height = width, alt = "" }) {
    if (typeof src !== "string" || !src || /[\\?#%]/.test(src) || /^(?:[a-z]+:|\/\/)/i.test(src) || src.startsWith("/") || src.split("/").includes("..")) {
      throw new Error("logo src must be a project-local relative asset path");
    }
    const root = element("div", "motion-logo", undefined, { width: `${width}px`, height: `${height}px` });
    const image = element("img", "motion-logo-image");
    image.src = src; image.alt = alt;
    root.append(image);
    return { root, image };
  }

  // Keep final advances, emphasis and explicit line breaks while wrapping text.
  function splitText(node, { by = "word", locale = "en" } = {}) {
    if (!["word", "character"].includes(by)) throw new Error("split mode must be word or character");
    const existing = [...node.querySelectorAll('.motion-text-part')];
    if (existing.length) return existing;
    const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT);
    const textNodes = [];
    while (walker.nextNode()) {
      if (!walker.currentNode.parentElement.closest('script,style,textarea,svg')) textNodes.push(walker.currentNode);
    }
    const parts = [];
    for (const textNode of textNodes) {
      const text = textNode.nodeValue;
      const chunks = by === "word" ? text.split(/(\s+)/) :
        [...new Intl.Segmenter(locale, { granularity: "grapheme" }).segment(text)].map(item => item.segment);
      const fragment = document.createDocumentFragment();
      for (const chunk of chunks) {
        if (!chunk) continue;
        if (/^\s+$/.test(chunk)) { fragment.append(document.createTextNode(chunk)); continue; }
        const span = element("span", "motion-text-part", chunk);
        fragment.append(span); parts.push(span);
      }
      textNode.replaceWith(fragment);
    }
    return parts;
  }

  // Blur/translate/stagger a phrase using reversible, explicit endpoints.
  function revealText(timeline, node, { at = 0, duration = 0.45, stagger = 0.06, y = 24, blur = 8, by = "word" } = {}) {
    const parts = splitText(node, { by });
    timeline.fromTo(parts, { opacity: 0, y, filter: `blur(${blur}px)` },
      { opacity: 1, y: 0, filter: "blur(0px)", duration, stagger, ease: "power3.out", immediateRender: true }, at);
    return parts;
  }

  // Character opacity events replace wall-clock typing and callback mutation.
  function typeText(timeline, node, { at = 0, duration = 1, locale = "en" } = {}) {
    const parts = splitText(node, { by: "character", locale });
    parts.forEach((part, i) => timeline.fromTo(part, { opacity: 0 },
      { opacity: 1, duration: 0.001, immediateRender: true }, at + duration * i / Math.max(1, parts.length)));
    return parts;
  }

  // Place a camera transform on a parent, preserving child/world coordinates.
  function camera(timeline, world, { at = 0, duration = 0.8, from, to, ease = "power3.inOut" }) {
    if (!from || !to) throw new Error("camera requires explicit from and to transforms");
    timeline.fromTo(world, { ...from, transformOrigin: "0 0" },
      { ...to, duration, ease, immediateRender: false }, at);
  }

  // Move a cursor to a known hotspot and show a bounded click compression.
  function pointerMove(timeline, node, { at = 0, duration = 0.6, from, to, click = false }) {
    if (!from || !to) throw new Error("pointerMove requires from/to points");
    timeline.fromTo(node, { x: from[0], y: from[1] },
      { x: to[0], y: to[1], duration, ease: "power3.inOut", immediateRender: false }, at);
    if (click) {
      timeline.fromTo(node, { scale: 1 }, { scale: 0.82, duration: 0.09, immediateRender: false }, at + duration);
      timeline.fromTo(node, { scale: 0.82 }, { scale: 1, duration: 0.16, ease: "power2.out", immediateRender: false }, at + duration + 0.09);
    }
  }

  // Reveal a vector stroke without depending on drawing callbacks.
  function drawPath(timeline, path, { at = 0, duration = 1, ease = "power2.inOut" } = {}) {
    const length = path.getTotalLength();
    path.style.strokeDasharray = String(length);
    timeline.fromTo(path, { strokeDashoffset: length }, { strokeDashoffset: 0, duration, ease, immediateRender: true }, at);
  }

  // Sample a function into SVG points; the same topology permits shape morphs.
  function curvePath(fn, { width = 800, height = 220, samples = 120 } = {}) {
    if (!Number.isInteger(samples) || samples < 2) throw new Error("samples must be an integer >= 2");
    return Array.from({ length: samples + 1 }, (_, i) => {
      const y = fn(i / samples);
      if (!Number.isFinite(y)) throw new Error("curve function must return finite values");
      return `${i ? "L" : "M"}${(i / samples * width).toFixed(3)},${(height * (1 - y)).toFixed(3)}`;
    }).join(" ");
  }

  // Resize a persistent shape (dot, badge, button) without scaling its child type.
  function morphBox(timeline, node, { at = 0, duration = 0.6, from, to, ease = "power3.inOut" }) {
    for (const endpoint of [from, to]) {
      if (!endpoint || ![endpoint.width, endpoint.height, endpoint.radius].every(Number.isFinite) ||
          endpoint.width <= 0 || endpoint.height <= 0 || endpoint.radius < 0) {
        throw new Error("morphBox needs positive width/height and nonnegative radius at each endpoint");
      }
    }
    const props = value => ({ width: value.width, height: value.height, borderRadius: value.radius,
      x: value.x ?? 0, y: value.y ?? 0 });
    timeline.fromTo(node, props(from), { ...props(to), duration, ease, immediateRender: false }, at);
  }

  // Sample a geometric path ahead of capture; playback callbacks are unnecessary.
  function followPath(timeline, node, path, { at = 0, duration = 1, samples = 60, ease = "none" } = {}) {
    if (!Number.isInteger(samples) || samples < 2 || samples > 2000) throw new Error("path samples must be 2–2000");
    const length = path.getTotalLength();
    const points = Array.from({length:samples+1}, (_,i)=>path.getPointAtLength(length*i/samples));
    timeline.set(node, {x:points[0].x,y:points[0].y}, at);
    points.slice(1).forEach((point,i)=>timeline.fromTo(node,
      {x:points[i].x,y:points[i].y},
      {x:point.x,y:point.y,duration:duration/samples,ease,immediateRender:false}, at+i*duration/samples));
  }

  // Build a clipped phrase with separate mask and text layers for word wipes.
  function maskedText({ text, fontSize = 72, width = 900 } = {}) {
    const root = element("div", "motion-text-mask", undefined,
      {overflow:"hidden",width:`${width}px`,padding:".12em 0"});
    const content = element("div", "motion-masked-text", text,
      {fontSize:`${fontSize}px`,lineHeight:"1.1",letterSpacing:"-.04em"});
    root.append(content);
    return {root,content};
  }

  // A ring uses a colored layer and an inset center rather than a raster logo.
  function halo({ size = 280, colors = ["#63dbff", "#ef69b0", "#ffd354"], center = "#08090c" } = {}) {
    const root = element("div", "motion-halo", undefined, { width: `${size}px`, height: `${size}px`, background: `conic-gradient(${colors.join(",")},${colors[0]})` });
    const hole = element("div", "motion-halo-hole", undefined, { background: center });
    root.append(hole);
    return { root, hole };
  }

  // Register the paused GSAP clock using HyperFrames' public composition contract.
  function register(id, timeline, duration) {
    if (!id || !Number.isFinite(duration) || duration <= 0 || !timeline.paused()) throw new Error("register needs an id, positive duration and paused timeline");
    const clock = { value: 0 };
    timeline.to(clock, { value: 1, duration, ease: "none" }, 0);
    // Initialize deferred GSAP properties before the first captured frame.
    // Suppress callbacks and return to zero so fresh and backward seeks share
    // the same canonical starting state, without starting a playback clock.
    timeline.totalTime(duration, true).totalTime(0, true);
    global.__timelines = global.__timelines || {};
    global.__timelines[id] = timeline;
    return timeline;
  }

  const api = { seconds, seeded, springAt, element, vector, theme, windowPanel, terminal, promptBar,
    menu, cursor, orb, logo, splitText, revealText, typeText, camera, pointerMove, drawPath, curvePath,
    morphBox, followPath, maskedText, halo, register };
  global.MotionKit = Object.freeze(api);
  if (typeof module !== "undefined") module.exports = api;
})(typeof window === "undefined" ? globalThis : window);
