import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { closeBrowser, framePlan, isWithin, parseArgs, publishOutput, startAssetServer, writeEncoderFrame } from '../helpers/motion_render.mjs';

test('exclusive end time produces exactly 360 frames for a 12 second 30fps export', () => {
  const plan = framePlan(12, 30);
  assert.equal(plan.count, 360);
  assert.equal(plan.lastTime, 359 / 30);
  assert.ok(plan.lastTime < plan.duration);
});

test('non-frame-aligned durations fail instead of silently changing the runtime', () => {
  assert.throws(() => framePlan(1.015, 30), /frame-aligned/);
  assert.equal(framePlan(1001 / 1000, 30000 / 1001).count, 30);
  for (const duration of [NaN, Infinity, 0, -1]) assert.throws(() => framePlan(duration, 30));
});

test('CLI rejects invalid delivery and impossible sample times before launch', () => {
  const base = ['scene.html', '-o', 'final.mp4', '--duration', '12'];
  assert.throws(() => parseArgs([...base, '--width', '1919']), /even integer/);
  assert.throws(() => parseArgs([...base, '--stills', '0,12']), /\[0, duration\)/);
  assert.throws(() => parseArgs([...base, '--poster-time', '-1']), /\[0, duration\)/);
  assert.throws(() => parseArgs([...base, '--preset', 'imaginary']), /Invalid/);
  const parsed = parseArgs([...base, '--stills-only', '--stills', '0,5.2']);
  assert.deepEqual(parsed.stills, [0, 5.2]);
  assert.equal(parsed['stills-only'], true);
});

test('asset boundary excludes siblings with shared prefixes and parent traversal', () => {
  assert.equal(isWithin('/tmp/scene', '/tmp/scene/assets/font.woff2'), true);
  assert.equal(isWithin('/tmp/scene', '/tmp/scene'), true);
  assert.equal(isWithin('/tmp/scene', '/tmp/scene-private/key.txt'), false);
  assert.equal(isWithin('/tmp/scene', '/tmp/scene/../secret.txt'), false);
});

test('completed Chrome releases inherited stdio without killing an exited process', async () => {
  const calls = [];
  const child = { exitCode: 0, signalCode: null, kill: () => calls.push('kill'), unref: () => calls.push('unref'), stdio: [null, { destroy: () => calls.push('stdout') }, { destroy: () => calls.push('stderr') }] };
  await closeBrowser({ process: () => child, close: async () => calls.push('close'), disconnect: () => calls.push('disconnect') });
  assert.deepEqual(calls, ['close', 'disconnect', 'stdout', 'stderr', 'unref']);
});

test('unresponsive owned Chrome cannot block shutdown indefinitely', async () => {
  const calls = [];
  const child = { exitCode: null, signalCode: null, kill: signal => calls.push(signal), unref: () => calls.push('unref'), stdio: [{ destroy: () => calls.push('destroy') }] };
  await closeBrowser({ process: () => child, close: () => new Promise(() => {}), disconnect: () => calls.push('disconnect') }, 10, () => {});
  assert.deepEqual(calls, ['SIGKILL', 'disconnect', 'destroy', 'unref']);
});

test('broken encoder pipe reports frame and actual ffmpeg failure', async () => {
  const pipeError = Object.assign(new Error('write EPIPE'), { code: 'EPIPE' });
  const encoder = { exitCode: null, stdin: { destroyed: false, write: (_bytes, callback) => callback(pipeError) } };
  const completion = Promise.reject(new Error('ffmpeg exited with code 1: No space left on device'));
  completion.catch(() => {});
  await assert.rejects(writeEncoderFrame(encoder, completion, Buffer.from('frame'), 300), /frame 301: ffmpeg exited with code 1: No space left on device/);
});

test('asset server supports real byte-range media requests while hashing the full source', async t => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'motion-range-'));
  const bytes = Buffer.from('ABCDEFGHIJ');
  await fs.writeFile(path.join(directory, 'sample.mp4'), bytes);
  await fs.writeFile(path.join(directory, 'empty.mp4'), Buffer.alloc(0));
  const serving = await startAssetServer(directory);
  t.after(async () => {
    serving.server.closeAllConnections();
    await new Promise(resolve => serving.server.close(resolve));
    await fs.rm(directory, { recursive: true });
  });
  const url = `${serving.origin}/sample.mp4`;
  const whole = await fetch(url);
  assert.equal(whole.status, 200);
  assert.equal(whole.headers.get('accept-ranges'), 'bytes');
  assert.equal(whole.headers.get('content-length'), '10');
  assert.equal(await whole.text(), 'ABCDEFGHIJ');
  for (const [range, expected, contentRange] of [
    ['bytes=2-5', 'CDEF', 'bytes 2-5/10'],
    ['bytes=7-', 'HIJ', 'bytes 7-9/10'],
    ['bytes=-3', 'HIJ', 'bytes 7-9/10'],
    ['bytes=-30', 'ABCDEFGHIJ', 'bytes 0-9/10'],
    ['bytes=8-9999999999999999999999999', 'IJ', 'bytes 8-9/10'],
    ['bytes=0-0', 'A', 'bytes 0-0/10'],
  ]) {
    const response = await fetch(url, { headers: { Range: range } });
    assert.equal(response.status, 206, range);
    assert.equal(response.headers.get('content-range'), contentRange);
    assert.equal(Number(response.headers.get('content-length')), expected.length);
    assert.equal(await response.text(), expected);
  }
  assert.deepEqual(serving.assets.get('sample.mp4'), { bytes: bytes.length, sha256: createHash('sha256').update(bytes).digest('hex') });
  for (const range of ['bytes=50-', 'bytes=5-4', 'bytes=-0', 'bytes=-', 'bytes=nope']) {
    const response = await fetch(url, { headers: { Range: range } });
    assert.equal(response.status, 416, range);
    assert.equal(response.headers.get('content-range'), 'bytes */10');
    assert.equal(await response.text(), '');
  }
  const empty = await fetch(`${serving.origin}/empty.mp4`, { headers: { Range: 'bytes=0-' } });
  assert.equal(empty.status, 416);
  assert.equal(empty.headers.get('content-range'), 'bytes */0');
  await empty.arrayBuffer();
  // Range is ignored for HEAD, unknown units, and unsupported multipart requests.
  for (const options of [{ method:'HEAD',headers:{Range:'bytes=2-5'} }, { headers:{Range:'items=0-2'} }, { headers:{Range:'bytes=0-1,4-5'} }]) {
    const response = await fetch(url, options);
    assert.equal(response.status, 200);
    assert.equal(response.headers.get('content-length'), '10');
    assert.equal(response.headers.get('content-range'), null);
    assert.equal(await response.text(), options.method === 'HEAD' ? '' : 'ABCDEFGHIJ');
  }
});


test('frame counts must remain finite and safely representable', () => {
  assert.throws(() => framePlan(Number.MAX_VALUE, 30), /frame-aligned/);
  assert.throws(() => framePlan(Number.MAX_SAFE_INTEGER + 1, 1), /frame-aligned/);
});

test('publication preserves existing files and dangling links unless overwrite is explicit', async t => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'motion-publish-'));
  t.after(() => fs.rm(directory, { recursive: true }));
  const temporary = path.join(directory, 'temp.mp4');
  const output = path.join(directory, 'final.mp4');
  await fs.writeFile(temporary, 'new');
  await fs.writeFile(output, 'existing');
  await assert.rejects(publishOutput(temporary, output), { code: 'EEXIST' });
  assert.equal(await fs.readFile(output, 'utf8'), 'existing');
  await fs.unlink(output);
  await fs.symlink(path.join(directory, 'absent.mp4'), output);
  await assert.rejects(publishOutput(temporary, output), { code: 'EEXIST' });
  assert.ok((await fs.lstat(output)).isSymbolicLink());
  await publishOutput(temporary, output, true);
  assert.equal(await fs.readFile(output, 'utf8'), 'new');
  await fs.unlink(output);
  await fs.writeFile(temporary, 'first');
  await publishOutput(temporary, output);
  assert.equal(await fs.readFile(output, 'utf8'), 'first');
  await assert.rejects(fs.stat(temporary), { code: 'ENOENT' });
});
