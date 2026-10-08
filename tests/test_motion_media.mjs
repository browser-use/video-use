import test from 'node:test';
import assert from 'node:assert/strict';
import {coverRect,celIndex,spriteRect,withMask,seekMedia} from '../skills/motion-design/runtime/media.mjs';

test('portrait and landscape cover crops preserve aspect and obey edge focus',()=>{
  assert.deepEqual(coverRect(1600,900,400,400),{sx:350,sy:0,sw:900,sh:900});
  assert.deepEqual(coverRect(900,1600,400,400,{focusY:1}),{sx:0,sy:700,sw:900,sh:900});
  const r=coverRect(900,1600,1920,1080,{zoom:2,focusX:0});
  assert.equal(r.sx,0);assert.ok(Math.abs(r.sw/r.sh-1920/1080)<1e-12);
  assert.throws(()=>coverRect(0,900,400,400));assert.throws(()=>coverRect(900,900,400,400,{zoom:.5}));
});
test('held cels support unequal durations and reversed/out-of-range seeking',()=>{
  const d=[.1,.4,.2];
  assert.deepEqual([.65,.01,.11,.51,-1,5].map(t=>celIndex(t,d)),[2,0,1,2,0,2]);
  assert.equal(celIndex(.71,d,{loop:true}),0);
  assert.equal(celIndex(-.1,d,{loop:true}),2);
  assert.throws(()=>celIndex(0,[0]));assert.throws(()=>celIndex(NaN,d));
});
test('sprite sheet supports arbitrary grid and rejects invalid frames',()=>{
  assert.deepEqual(spriteRect(5,{columns:3,rows:2,width:900,height:400}),{sx:600,sy:200,sw:300,sh:200});
  assert.throws(()=>spriteRect(6,{columns:3,rows:2,width:900,height:400}));
});
test('mask scope restores canvas state even when authored drawing fails',()=>{
  let depth=0;const c={save(){depth++;},restore(){depth--;},clip(){}};
  assert.throws(()=>withMask(c,{},()=>{throw new Error('bad draw');}));assert.equal(depth,0);
});
test('media helper pauses and waits for decoded seek, and rejects expired source time',async()=>{
  class Media extends EventTarget {readyState=2;duration=5;_time=0;paused=false;pause(){this.paused=true;}get currentTime(){return this._time;}set currentTime(t){this._time=t;queueMicrotask(()=>this.dispatchEvent(new Event('seeked')));}}
  const m=new Media();await seekMedia(m,3);await seekMedia(m,1);
  assert.equal(Math.round(m.currentTime*1e6),1e6);assert.equal(m.paused,true);
  await assert.rejects(seekMedia(m,5));
});
test('same-target concurrent media seek still waits for pending decode',async()=>{
  class Media extends EventTarget {readyState=2;duration=5;currentTime=1;seeking=true;pause(){}}
  const m=new Media();let done=false;
  const promise=seekMedia(m,1).then(()=>{done=true;});
  await Promise.resolve();assert.equal(done,false);
  m.seeking=false;m.dispatchEvent(new Event('seeked'));await promise;assert.equal(done,true);
});
test('seeked with decoded data cannot falsely pass after a server clamps the requested time',async()=>{
  class UnseekableMedia extends EventTarget {readyState=4;duration=5;pause(){}get currentTime(){return 0;}set currentTime(_time){queueMicrotask(()=>this.dispatchEvent(new Event('seeked')));}}
  await assert.rejects(seekMedia(new UnseekableMedia(),2),/requested 2, actual 0.*byte-range/);
});
test('fractional source PTS rounds forward and repeated final-frame seeks do not repeat decoding',async()=>{
  class QuantizedMedia extends EventTarget {
    readyState=4;duration=2;_time=0;seeks=0;pause(){}
    get currentTime(){return this._time;}
    set currentTime(value){this.seeks++;this._time=Math.floor(value*1e6)/1e6;queueMicrotask(()=>this.dispatchEvent(new Event('seeked')));}
  }
  const media=new QuantizedMedia(),last=47/24;
  await seekMedia(media,last);await seekMedia(media,last);
  assert.equal(media.seeks,1);assert.equal(media.currentTime,1.958334);
  assert.ok(media.currentTime>=last);
  await seekMedia(media,25/24);
  assert.equal(media.currentTime,1.041667,'A binary float just below a decimal tick must not truncate back to the preceding microsecond');
});
test('a single microsecond step across a presentation boundary must seek instead of reusing the previous frame',async()=>{
  class QuantizedMedia extends EventTarget {
    readyState=4;duration=2;_time=.041666;seeks=0;pause(){}
    get currentTime(){return this._time;}
    set currentTime(value){this.seeks++;this._time=Math.round(value*1e6)/1e6;queueMicrotask(()=>this.dispatchEvent(new Event('seeked')));}
  }
  const media=new QuantizedMedia();
  await seekMedia(media,1/24);
  assert.equal(media.seeks,1);assert.equal(media.currentTime,.041667);
  await seekMedia(media,1/24-1e-7);
  assert.equal(media.seeks,1,'Requests in the same representable microsecond use the same decoded frame');
});
test('a source time that rounds onto the duration fails explicitly rather than seeking an ambiguous ended frame',async()=>{
  const media={readyState:4,duration:2,currentTime:0,pause(){}};
  await assert.rejects(seekMedia(media,2-1e-7),/rounds to media duration/);
  assert.equal(media.currentTime,0);
});
test('explicit unequal source frame intervals sample their interiors without a nominal FPS assumption',async()=>{
  class Media extends EventTarget {readyState=4;duration=3;_time=0;pause(){}get currentTime(){return this._time;}set currentTime(value){this._time=Math.round(value*1e6)/1e6;queueMicrotask(()=>this.dispatchEvent(new Event('seeked')));}}
  const media=new Media();
  const result=await seekMedia(media,25/24,{frameEnd:26/24});
  assert.equal(media.currentTime,1.0625);
  assert.deepEqual(result.frameInterval,{start:25/24,end:26/24});
  await seekMedia(media,.07,{frameEnd:.31});
  assert.equal(media.currentTime,.19);
  await assert.rejects(seekMedia(media,1,{frameEnd:1}));
  await assert.rejects(seekMedia(media,1,{frameEnd:4}));
  await assert.rejects(seekMedia(media,0,{frameEnd:.0000004}),/collapses/);
});
test('small playback-clock reporting error is separate from decoded-frame identity',async()=>{
  class ApproximateMedia extends EventTarget {readyState=4;duration=3;_time=0;pause(){}get currentTime(){return this._time;}set currentTime(value){this._time=(Math.round(value*1e6)-1)/1e6;queueMicrotask(()=>this.dispatchEvent(new Event('seeked')));}}
  const result=await seekMedia(new ApproximateMedia(),1);
  assert.equal(result.landedTime,.999999);
  assert.equal(result.requestedTime,1);
});

test('failed and timed out media seeks release their event listeners', async () => {
  class Media extends EventTarget {
    readyState = 2; duration = 5; currentTime = 0;
    listeners = new Set();
    pause() {}
    addEventListener(name, callback, options) {
      this.listeners.add(callback);
      super.addEventListener(name, callback, options);
    }
    removeEventListener(name, callback) {
      this.listeners.delete(callback);
      super.removeEventListener(name, callback);
    }
  }
  const stalled = new Media();
  await assert.rejects(seekMedia(stalled, 1, { timeout: 10 }), /seeked timeout/);
  assert.equal(stalled.listeners.size, 0);
  const failed = new Media();
  const seeking = seekMedia(failed, 2);
  failed.dispatchEvent(new Event('error'));
  await assert.rejects(seeking, /seeked failed/);
  assert.equal(failed.listeners.size, 0);
});

test('metadata and decoded data must be ready before a seek completes', async () => {
  class Media extends EventTarget {
    readyState = 0; duration = 5; currentTime = 0;
    pause() {}
  }
  const media = new Media();
  let completed = false;
  const seeking = seekMedia(media, 0).then(() => { completed = true; });
  media.readyState = 1;
  media.dispatchEvent(new Event('loadedmetadata'));
  await Promise.resolve();
  assert.equal(completed, false);
  media.readyState = 2;
  media.dispatchEvent(new Event('loadeddata'));
  await seeking;
  assert.equal(completed, true);
});
