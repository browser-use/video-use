import test from 'node:test';import assert from 'node:assert/strict';
import {twoBoneIK} from '../skills/motion-design/runtime/rig.mjs';
const close=(a,b)=>assert.ok(Math.abs(a-b)<1e-8,`${a} != ${b}`);
test('arbitrary roots and target rotations preserve limb lengths',()=>{
 for(const target of [{x:30,y:25},{x:-21,y:53},{x:-33,y:-3}]){
  const root={x:2,y:5};const p=twoBoneIK({root,target,upper:40,lower:32});
  close(Math.hypot(p.elbow.x-root.x,p.elbow.y-root.y),40);close(Math.hypot(p.end.x-p.elbow.x,p.end.y-p.elbow.y),32);
  close(p.end.x,target.x);close(p.end.y,target.y);assert.equal(p.reachable,true);
 }
});
test('bend choice mirrors elbows; unreachable chain reaches maximum without stretching',()=>{
 const spec={root:{x:0,y:0},target:{x:50,y:0},upper:40,lower:40};
 const a=twoBoneIK(spec),b=twoBoneIK({...spec,bend:-1});close(a.elbow.x,b.elbow.x);close(a.elbow.y,-b.elbow.y);
 const far=twoBoneIK({...spec,target:{x:500,y:0}});close(far.end.x,80);assert.equal(far.reachable,false);
});
test('folded and unequal zero-distance targets remain finite and preserve lengths',()=>{
 for(const lower of [40,20]){const p=twoBoneIK({root:{x:0,y:0},target:{x:0,y:0},upper:40,lower});close(Math.hypot(p.elbow.x-p.end.x,p.elbow.y-p.end.y),lower);}
 assert.throws(()=>twoBoneIK({root:{x:0,y:0},target:{x:0,y:0},upper:0,lower:1}));
});
