/** Small, theme-free motion building blocks. All timeline sampling uses absolute time. */
const finite = (value, label) => {
  if (typeof value !== 'number' || !Number.isFinite(value)) throw new TypeError(`${label} must be a finite number`);
  return value;
};
export const clamp = (value, min = 0, max = 1) => {
  finite(value, 'value'); finite(min, 'min'); finite(max, 'max');
  if (max < min) throw new RangeError('max must be at least min');
  return Math.min(max, Math.max(min, value));
};
export const lerp = (from, to, amount) => finite(from, 'from') + (finite(to, 'to') - from) * finite(amount, 'amount');

const curves = Object.freeze({
  linear: t => t,
  inCubic: t => t * t * t,
  outCubic: t => 1 - (1 - t) ** 3,
  inOutCubic: t => t < .5 ? 4 * t ** 3 : 1 - (-2 * t + 2) ** 3 / 2,
  smooth: t => t * t * (3 - 2 * t),
  outBack: t => 1 + 2.70158 * (t - 1) ** 3 + 1.70158 * (t - 1) ** 2,
});
export function ease(curve = 'linear') {
  const fn = typeof curve === 'function' ? curve : curves[curve];
  if (typeof fn !== 'function') throw new RangeError(`Unknown easing: ${String(curve)}`);
  return amount => finite(fn(clamp(amount)), 'easing result');
}

function poseKeys(value) {
  if (typeof value === 'number') { finite(value, 'keyframe value'); return null; }
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new TypeError('A value must be a number or flat numeric pose');
  const keys = Object.keys(value).sort();
  if (!keys.length) throw new TypeError('A pose needs at least one numeric property');
  keys.forEach(key => finite(value[key], `pose.${key}`));
  return keys;
}

/** Numeric pose interpolation is renderer agnostic: positions, colors, rig controls, etc. */
export function interpolatePose(from, to, amount) {
  finite(amount, 'amount');
  const a = poseKeys(from), b = poseKeys(to);
  if (JSON.stringify(a) !== JSON.stringify(b)) throw new TypeError('Pose properties must match');
  if (a === null) return lerp(from, to, amount);
  return Object.fromEntries(a.map(key => [key, lerp(from[key], to[key], amount)]));
}

/** Ease belongs to the outgoing key. Unevenly spaced keys and end holds are intentional. */
export function keyframes(keys) {
  if (!Array.isArray(keys) || keys.length < 1) throw new TypeError('At least one keyframe is required');
  let previous = -Infinity;
  let shape;
  const frozen = keys.map(({ time, value, ease: curve = 'linear' }, index) => {
    finite(time, 'keyframe time');
    if (time <= previous) throw new RangeError('Keyframe times must be strictly increasing');
    previous = time;
    const signature = JSON.stringify(poseKeys(value));
    if (index && signature !== shape) throw new TypeError('All keyframes must have matching numeric value properties');
    shape = signature;
    return { time, value: typeof value === 'number' ? value : { ...value }, curve: ease(curve) };
  });
  const copy = value => typeof value === 'number' ? value : { ...value };
  return time => {
    finite(time, 'sample time');
    if (time <= frozen[0].time) return copy(frozen[0].value);
    if (time >= frozen.at(-1).time) return copy(frozen.at(-1).value);
    let low = 0, high = frozen.length - 1;
    while (high - low > 1) {
      const mid = Math.floor((low + high) / 2);
      if (frozen[mid].time <= time) low = mid; else high = mid;
    }
    const a = frozen[low], b = frozen[high];
    return interpolatePose(a.value, b.value, a.curve((time - a.time) / (b.time - a.time)));
  };
}

/** A shot is active on [start, start + duration); local time holds outside that range. */
export function clip(time, { start = 0, duration, rate = 1 } = {}) {
  finite(time, 'time'); finite(start, 'start'); finite(duration, 'duration'); finite(rate, 'rate');
  if (duration <= 0 || rate <= 0) throw new RangeError('duration and rate must be positive');
  const progress = clamp((time - start) / duration);
  return { active: time >= start && time < start + duration, progress, time: progress * duration * rate };
}

/** Stateful construction helper. Create fixed data once; do not advance it inside seek(). */
export function seededRandom(seed) {
  if (typeof seed !== 'string' && !(typeof seed === 'number' && Number.isFinite(seed))) throw new TypeError('seed must be a string or finite number');
  let state = 2166136261;
  for (const char of String(seed)) state = Math.imul(state ^ char.codePointAt(0), 16777619) >>> 0;
  return () => {
    state = (state + 0x6D2B79F5) >>> 0;
    let value = Math.imul(state ^ state >>> 15, 1 | state);
    value ^= value + Math.imul(value ^ value >>> 7, 61 | value);
    return ((value ^ value >>> 14) >>> 0) / 4294967296;
  };
}

function checkMatrix(value) {
  if (!Array.isArray(value) || value.length !== 6) throw new TypeError('A 2D matrix needs six values');
  value.forEach(number => finite(number, 'matrix value'));
  return value;
}
/** Canvas/SVG affine order [a,b,c,d,e,f]. Result applies local first, then parent. */
export function compose2D(parent, local) {
  const [a,b,c,d,e,f] = checkMatrix(parent), [g,h,i,j,k,l] = checkMatrix(local);
  return [a*g+c*h, b*g+d*h, a*i+c*j, b*i+d*j, a*k+c*l+e, b*k+d*l+f];
}
export function matrix2D({ x=0, y=0, rotation=0, scaleX=1, scaleY=1, skewX=0, skewY=0, anchorX=0, anchorY=0 } = {}) {
  [x,y,rotation,scaleX,scaleY,skewX,skewY,anchorX,anchorY].forEach(number => finite(number, 'transform value'));
  const c = Math.cos(rotation), s = Math.sin(rotation);
  const result = compose2D([c,s,-s,c,x,y], [scaleX,Math.tan(skewY)*scaleX,Math.tan(skewX)*scaleY,scaleY,0,0]);
  result[4] -= result[0]*anchorX + result[2]*anchorY;
  result[5] -= result[1]*anchorX + result[3]*anchorY;
  return checkMatrix(result);
}
export function apply2D(matrix, { x, y }) {
  const [a,b,c,d,e,f] = checkMatrix(matrix);
  finite(x, 'point.x'); finite(y, 'point.y');
  return { x:a*x+c*y+e, y:b*x+d*y+f };
}
export function withTransform(context, transform, draw) {
  if (typeof draw !== 'function') throw new TypeError('draw must be a function');
  const matrix = Array.isArray(transform) ? checkMatrix(transform) : matrix2D(transform);
  context.save();
  try { context.transform(...matrix); return draw(context); } finally { context.restore(); }
}

const segmenter = typeof Intl.Segmenter === 'function' ? new Intl.Segmenter(undefined, { granularity:'grapheme' }) : null;
export const graphemes = text => {
  if (typeof text !== 'string') throw new TypeError('text must be a string');
  return segmenter ? Array.from(segmenter.segment(text), item => item.segment) : Array.from(text);
};
export function measureText(context, text, { size=100, family='sans-serif', weight=400, tracking=0, lineHeight=1.15 } = {}) {
  finite(size, 'font size'); finite(tracking, 'tracking'); finite(lineHeight, 'lineHeight');
  if (size <= 0 || lineHeight <= 0) throw new RangeError('font size and lineHeight must be positive');
  if (typeof text !== 'string') throw new TypeError('text must be a string');
  if (typeof family !== 'string' || !family.trim()) throw new TypeError('font family must be a nonempty string');
  const oldFont = context.font;
  try {
    context.font = `${weight} ${size}px ${family}`;
    const lines = text.split('\n').map(line => ({ text:line, width:Math.max(0, context.measureText(line).width + Math.max(0,graphemes(line).length-1)*tracking) }));
    return { size, width:Math.max(...lines.map(line => line.width)), height:size*lineHeight*lines.length, lineHeight:size*lineHeight, lines };
  } finally { context.font = oldFont; }
}
/** Explicit newlines are preserved. Layout/word wrapping remains the scene author's choice. */
export function fitText(context, text, { width, height=Infinity, minSize=1, maxSize=256, ...style } = {}) {
  finite(width, 'fit width'); finite(minSize, 'minSize'); finite(maxSize, 'maxSize');
  if (height !== Infinity) finite(height, 'fit height');
  if (width <= 0 || height <= 0 || minSize <= 0 || maxSize < minSize) throw new RangeError('Fit dimensions must be positive and maxSize >= minSize');
  const fits = layout => layout.width <= width && layout.height <= height;
  let low = minSize, high = maxSize;
  for (let i=0; i<28; i++) {
    const middle = (low+high)/2;
    if (fits(measureText(context,text,{...style,size:middle}))) low=middle; else high=middle;
  }
  const layout = measureText(context,text,{...style,size:low});
  return { ...layout, fits:fits(layout) };
}
