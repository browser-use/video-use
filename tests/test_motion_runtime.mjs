import test from 'node:test';
import assert from 'node:assert/strict';
import { ease, keyframes, clip, seededRandom, interpolatePose, matrix2D, compose2D, apply2D, withTransform, measureText, fitText, graphemes } from '../skills/motion-design/runtime/motion.mjs';

const near = (actual,expected) => assert.ok(Math.abs(actual-expected)<1e-6,`${actual} != ${expected}`);
test('uneven timeline samples exact boundaries and arbitrary seek order', () => {
  const sample = keyframes([{time:2,value:0,ease:'inCubic'},{time:3,value:8},{time:7,value:16}]);
  for (const [time,expected] of [[5,12],[2.5,1],[8,16],[-3,0],[3,8],[7,16],[2,0],[2.5,1]]) near(sample(time),expected);
});
test('poses are independent snapshots and arbitrary property names work', () => {
  const first={jaw:.1,iris:4}, last={iris:20,jaw:.9};
  const sample=keyframes([{time:0,value:first},{time:10,value:last}]);
  first.jaw=300; last.iris=-100;
  assert.deepEqual(sample(5),{iris:12,jaw:.5});
  const endpoint=sample(-1); endpoint.iris=0;
  assert.equal(sample(-1).iris,4);
  assert.deepEqual(interpolatePose({saturation:0},{saturation:1},1.2),{saturation:1.2});
});
test('single key and custom easing are supported without weakening validation', () => {
  assert.equal(keyframes([{time:-4,value:9}])(200),9);
  assert.equal(keyframes([{time:0,value:2,ease:t=>t*t},{time:1,value:6}])(.5),3);
  for (const keys of [[],[{time:0,value:0},{time:0,value:1}],[{time:1,value:0},{time:0,value:1}],[{time:NaN,value:0}],[{time:0,value:{x:1}},{time:1,value:{y:1}}],[{time:0,value:Infinity}],[{time:0,value:{x:{z:1}}}]]) assert.throws(()=>keyframes(keys));
  assert.throws(()=>ease('invented'));
  assert.throws(()=>ease(()=>NaN)(.5));
  assert.throws(()=>keyframes([{time:0,value:0}])(Infinity));
});
test('shot boundaries use half-open intervals and playback rate only changes local time', () => {
  assert.deepEqual(clip(1,{start:2,duration:3,rate:2}),{active:false,progress:0,time:0});
  assert.deepEqual(clip(2,{start:2,duration:3,rate:2}),{active:true,progress:0,time:0});
  assert.deepEqual(clip(3.5,{start:2,duration:3,rate:2}),{active:true,progress:.5,time:3});
  assert.deepEqual(clip(5,{start:2,duration:3,rate:2}),{active:false,progress:1,time:6});
  for (const options of [{duration:0},{duration:-1},{duration:2,rate:0},{duration:2,start:NaN}]) assert.throws(()=>clip(0,options));
});
test('seeded construction gives repeatable varied distributions', () => {
  const series=seed=>Array.from({length:200},seededRandom(seed));
  assert.deepEqual(series('forest'),series('forest'));
  assert.notDeepEqual(series('forest'),series('ocean'));
  assert.ok(series(0).every(value=>value>=0&&value<1));
  assert.ok(new Set(series(0)).size>190);
  assert.throws(()=>seededRandom({}));
});
test('nested transforms preserve pivots and parent order', () => {
  const parent=matrix2D({x:100,y:80,rotation:Math.PI/2});
  const child=matrix2D({x:20,y:0,scaleX:2,scaleY:3,anchorX:4,anchorY:5});
  const anchor=apply2D(compose2D(parent,child),{x:4,y:5});
  near(anchor.x,100); near(anchor.y,100);
  const point=apply2D(compose2D(parent,child),{x:5,y:6});
  near(point.x,97); near(point.y,102);
  assert.throws(()=>matrix2D({rotation:NaN}));
  assert.throws(()=>compose2D([1,2],parent));
});
test('drawing scope restores the parent even if a component fails', () => {
  let depth=0;
  const ctx={save:()=>depth++,restore:()=>depth--,transform:()=>{}};
  assert.throws(()=>withTransform(ctx,{x:10},()=>{throw Error('component failed');}));
  assert.equal(depth,0);
});
const context={font:'old',measureText(text){return {width:graphemes(text).length*parseFloat(this.font.split(' ')[1])*.6};}};
test('fitting adapts to different content and explicit multiline layouts', () => {
  const short=fitText(context,'Hi',{width:300,maxSize:180});
  const long=fitText(context,'A much longer thought',{width:300,maxSize:180});
  assert.ok(short.size>long.size*5);
  assert.ok(long.fits&&long.width<=300);
  const multiline=fitText(context,'One\nTwo',{width:800,height:200,maxSize:300,lineHeight:1});
  near(multiline.size,100); assert.equal(multiline.lines.length,2);
  assert.equal(context.font,'old');
  assert.equal(fitText(context,'Too wide',{width:1,minSize:30,maxSize:90}).fits,false);
  const empty=fitText(context,'',{width:100,height:100,maxSize:50});
  assert.ok(empty.fits); assert.equal(empty.width,0);
});
test('tracking counts graphemes rather than splitting joined emoji or accents', () => {
  assert.equal(graphemes('á👩‍👩‍👧‍👦').length,2);
  const measured=measureText(context,'á👩‍👩‍👧‍👦',{size:10,tracking:3});
  assert.equal(measured.width,15);
  for (const options of [{width:0},{width:100,minSize:20,maxSize:10},{width:100,height:-1}]) assert.throws(()=>fitText(context,'text',options));
  assert.throws(()=>measureText(context,42));
});
