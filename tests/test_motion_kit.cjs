/** Pure animation math and clock tests run with node --test, without npm install. */
const test = require('node:test');
const assert = require('node:assert/strict');
const M = require('../skills/motion-design/assets/motion-kit.js');

test('integer frames give exact half-open sample times', () => {
  assert.equal(M.seconds(0,30),0);
  assert.equal(M.seconds(239,30),239/30);
  assert.throws(()=>M.seconds(-1));
  assert.throws(()=>M.seconds(.5));
  assert.throws(()=>M.seconds(2,0));
});
test('procedural randomness is repeatable and bounded', () => {
  const a=M.seeded(22), b=M.seeded(22), c=M.seeded(23);
  for(let i=0;i<100;i++) { const x=a(); assert.equal(x,b()); assert.ok(x>=0&&x<1); }
  assert.notEqual(a(),c());
});
test('spring begins at rest and converges without a playback loop', () => {
  assert.equal(M.springAt(-1),0);
  assert.equal(M.springAt(0),0);
  assert.ok(Math.abs(M.springAt(3)-1)<1e-7);
  assert.throws(()=>M.springAt(1,{damping:0}));
});
test('curve points preserve topology for transforms', () => {
  assert.equal(M.curvePath(x=>x,{width:10,height:10,samples:2}),'M0.000,10.000 L5.000,5.000 L10.000,0.000');
  assert.throws(()=>M.curvePath(()=>NaN));
  assert.throws(()=>M.curvePath(x=>x,{samples:1}));
});
test('logo rejects network and traversal paths before accessing DOM', () => {
  for(const src of ['https://example.com/logo.svg','//example.com/logo.svg','../logo.svg','/logo.svg','data:x','%2e%2e/logo.svg','\\\\example.com/logo.svg']) {
    assert.throws(()=>M.logo({src}),/project-local/);
  }
});
test('shape continuity requires valid endpoint geometry',()=>{
  assert.throws(()=>M.morphBox(null,null,{from:{width:0,height:10,radius:2},to:{width:10,height:10,radius:2}}),/positive/);
  assert.throws(()=>M.followPath(null,null,null,{samples:0}),/samples/);
});
test('registration initializes deferred state without playing callbacks',()=>{
  const visits=[];
  const timeline={paused:()=>true,to:()=>{},totalTime(t,suppress){visits.push([t,suppress]);return this;}};
  assert.equal(M.register('contract-test',timeline,4),timeline);
  assert.deepEqual(visits,[[4,true],[0,true]]);
  assert.equal(globalThis.__timelines['contract-test'],timeline);
  assert.throws(()=>M.register('bad',{paused:()=>false},4),/paused/);
});
