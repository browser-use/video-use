import test from 'node:test';
import assert from 'node:assert/strict';
import { applyHomography, homographyToMatrix3d, createTrackSampler } from '../skills/motion-design/runtime/tracking.mjs';

const identity=[[1,0,0],[0,1,0],[0,0,1]];
const reference=[[20,10],[140,10],[140,70],[20,70]];
const near=(actual,expected)=>assert.ok(Math.abs(actual-expected)<1e-8,`${actual} != ${expected}`);
const frame=(index,time,status='tracked',H=identity)=>({index,time,sourceTime:time+12,status,homography:status==='lost'?null:H.map(row=>[...row]),quad:status==='lost'?null:reference.map(([x,y])=>{const p=applyHomography(H,{x,y});return [p.x,p.y];}),features:24,inliers:20,confidence:status==='lost'?0:.83});
function fixture(){return {schemaVersion:1,source:{width:160,height:90,frameCount:4,avgFrameRate:'30/1',duration:.2},initialQuad:reference.map(point=>[...point]),frames:[frame(0,0,'initialized'),frame(1,.03),frame(2,.09,'lost'),frame(3,.15)]};}

test('true perspective mapping divides homogeneous coordinates and preserves scaled matrices',()=>{
  const H=[[2,.5,12],[-.2,1.5,-3],[.01,.002,1]];
  const p=applyHomography(H,{x:30,y:20});
  near(p.x,82/1.34);near(p.y,21/1.34);
  assert.deepEqual(applyHomography(identity,{x:160,y:90}),{x:160,y:90});
  const scaled=H.map(row=>row.map(value=>value*1e-12));
  const q=applyHomography(scaled,{x:30,y:20});near(q.x,p.x);near(q.y,p.y);
  const negative=homographyToMatrix3d(identity.map(row=>row.map(value=>-value)),{sourceWidth:160,sourceHeight:90});
  assert.ok(negative[15]>0,'Equivalent negative homogeneous scale must not place the CSS plane behind the camera');
});
test('CSS matrix maps original source boundaries into a scaled and offset video placement',()=>{
  const matrix=homographyToMatrix3d(identity,{sourceWidth:160,sourceHeight:90,outputWidth:800,outputHeight:360,offsetX:40,offsetY:80});
  function project([x,y]){const w=matrix[3]*x+matrix[7]*y+matrix[15];return {x:(matrix[0]*x+matrix[4]*y+matrix[12])/w,y:(matrix[1]*x+matrix[5]*y+matrix[13])/w};}
  assert.deepEqual(project([0,0]),{x:40,y:80});
  assert.deepEqual(project([160,90]),{x:840,y:440});
  const H=[[1,.1,5],[.05,1,2],[.001,-.001,1]];
  const m=homographyToMatrix3d(H,{sourceWidth:160,sourceHeight:90,outputWidth:800,outputHeight:360,offsetX:40,offsetY:80});
  const p=applyHomography(H,{x:75,y:42}),w=m[3]*75+m[7]*42+m[15];
  near((m[0]*75+m[4]*42+m[12])/w,p.x*5+40);
  near((m[1]*75+m[5]*42+m[13])/w,p.y*4+80);
});
test('nonuniform frame times hold actual observations and hide entire loss intervals',()=>{
  const sample=createTrackSampler(fixture());
  for(const [time,visible,index] of [[.14,false,2],[.03,true,1],[0,true,0],[.199,true,3],[.089,true,1],[.09,false,2],[.15,true,3],[.01,true,0],[.14,false,2]]){
    const result=sample(time);assert.equal(result.visible,visible);assert.equal(result.frame.index,index);
    if(!visible)assert.equal(result.frame.homography,null);
  }
  assert.equal(sample(-.01).visible,false);assert.equal(sample(-.01).frame,null);
  assert.equal(sample(.2).visible,false);assert.equal(sample(3).frame,null);
});
test('sampler snapshots input and returns fresh geometry for reverse-safe consumers',()=>{
  const data=fixture(),sample=createTrackSampler(data);
  data.frames[1].homography[0][2]=900;
  const result=sample(.05);result.frame.homography[1][2]=300;result.frame.quad[0][0]=1000;
  assert.equal(sample(.05).frame.homography[0][2],0);
  assert.equal(sample(.05).frame.homography[1][2],0);
  assert.equal(sample(.05).frame.quad[0][0],20);
});
test('initial quad uses source pixels but observed plane may legitimately leave the image',()=>{
  const data=fixture();data.initialQuad[0][0]=-1;assert.throws(()=>createTrackSampler(data),/source dimensions/);
  const outside=fixture();outside.frames[1]=frame(1,.03,'tracked',[[1,0,200],[0,1,0],[0,0,1]]);
  const sample=createTrackSampler(outside);assert.equal(sample(.05).visible,true);assert.ok(sample(.05).frame.quad.every(([x])=>x>160));
});
test('unknown duration covers exactly one nominal period after last observation',()=>{
  const data=fixture();delete data.source.duration;data.source.avgFrameRate='30000/1001';
  const sample=createTrackSampler(data),end=.15+1001/30000;
  assert.equal(sample(end-1e-9).visible,true);assert.equal(sample(end).visible,false);
});
test('lost frames suppress stale geometry and explicit reasons survive',()=>{
  const data=fixture();data.frames[2].homography=identity;data.frames[2].quad=reference;data.frames[2].reason='insufficient texture';
  const result=createTrackSampler(data)(.1);
  assert.equal(result.visible,false);assert.equal(result.frame.homography,null);assert.equal(result.reason,'insufficient texture');
});
test('malformed matrices, horizons, folded quads and invalid metadata fail explicitly',()=>{
  assert.throws(()=>applyHomography([[1,0,0],[0,0,0],[0,0,1]],{x:0,y:0}),/singular/);
  assert.throws(()=>applyHomography([[1,0,0],[0,1,0],[1,0,-1]],{x:1,y:0}),/horizon/);
  assert.throws(()=>homographyToMatrix3d(identity,{sourceWidth:0,sourceHeight:90}),/positive/);
  const mutations=[data=>data.frames[1].time=0,data=>data.source.avgFrameRate='0/0',data=>data.frames[1].confidence=2,data=>data.frames[1].homography[0][0]=Infinity,data=>data.frames[1].quad=[[0,0],[100,100],[100,0],[0,100]],data=>data.source.duration='invalid',data=>data.frames[1].index=4];
  for(const mutate of mutations){const data=fixture();mutate(data);assert.throws(()=>createTrackSampler(data));}
  assert.throws(()=>createTrackSampler(fixture())(NaN));
});
