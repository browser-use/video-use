/** Browser consumption of measured planar tracks; no estimation or scene presets. */
const finite=(value,label)=>{if(typeof value!=='number'||!Number.isFinite(value))throw new TypeError(`${label} must be finite`);return value;};
const positive=(value,label)=>{finite(value,label);if(value<=0)throw new RangeError(`${label} must be positive`);return value;};

function matrix(value){
  if(!Array.isArray(value)||value.length!==3||value.some(row=>!Array.isArray(row)||row.length!==3))throw new TypeError('Homography must be a 3x3 matrix');
  const flat=value.flat();flat.forEach(number=>finite(number,'Homography entry'));
  const scale=Math.max(...flat.map(Math.abs))*(value[2][2]<0?-1:1);
  if(scale===0)throw new RangeError('Homography is singular');
  const h=value.map(row=>row.map(number=>number/scale));
  const [[a,b,c],[d,e,f],[g,i,j]]=h;
  const determinant=a*(e*j-f*i)-b*(d*j-f*g)+c*(d*i-e*g);
  if(!Number.isFinite(determinant)||determinant===0)throw new RangeError('Homography is singular');
  return h;
}

/** Homographies act on homogeneous source-pixel coordinates, with perspective divide. */
export function applyHomography(homography,{x,y}){
  finite(x,'point.x');finite(y,'point.y');
  const h=matrix(homography);
  const terms=[h[2][0]*x,h[2][1]*y,h[2][2]];
  const denominator=terms.reduce((a,b)=>a+b,0);
  if(!Number.isFinite(denominator)||Math.abs(denominator)<=Number.EPSILON*8*terms.reduce((sum,value)=>sum+Math.abs(value),0))throw new RangeError('Point maps to the projective horizon');
  const result={x:(h[0][0]*x+h[0][1]*y+h[0][2])/denominator,y:(h[1][0]*x+h[1][1]*y+h[1][2])/denominator};
  finite(result.x,'projected x');finite(result.y,'projected y');
  return result;
}

/**
 * Transform an overlay authored in original source pixels into the placed video box.
 * Apply with transform-origin: 0 0 on a sourceWidth x sourceHeight element.
 * Returned values use CSS column-major matrix3d order.
 */
export function homographyToMatrix3d(homography,{sourceWidth,sourceHeight,outputWidth=sourceWidth,outputHeight=sourceHeight,offsetX=0,offsetY=0}={}){
  [sourceWidth,sourceHeight,outputWidth,outputHeight].forEach(value=>positive(value,'source/output dimension'));
  finite(offsetX,'offsetX');finite(offsetY,'offsetY');
  const h=matrix(homography),sx=outputWidth/sourceWidth,sy=outputHeight/sourceHeight;
  const a=h[0].map((value,index)=>sx*value+offsetX*h[2][index]);
  const b=h[1].map((value,index)=>sy*value+offsetY*h[2][index]);
  // Use one homogeneous scale for x, y, and w. z remains on the same flat plane.
  const result=[a[0],b[0],0,h[2][0],a[1],b[1],0,h[2][1],0,0,1,0,a[2],b[2],0,h[2][2]];
  result.forEach(value=>finite(value,'CSS matrix entry'));
  return result;
}

function quad(value,label){
  if(!Array.isArray(value)||value.length!==4||value.some(point=>!Array.isArray(point)||point.length!==2))throw new TypeError(`${label} must have four xy points`);
  value.flat().forEach(number=>finite(number,`${label} coordinate`));
  const crosses=value.map((a,index)=>{
    const b=value[(index+1)%4],c=value[(index+2)%4];
    return (b[0]-a[0])*(c[1]-b[1])-(b[1]-a[1])*(c[0]-b[0]);
  });
  if(crosses.some(value=>!Number.isFinite(value)||value===0)||!crosses.every(value=>Math.sign(value)===Math.sign(crosses[0])))throw new RangeError(`${label} must be a convex nondegenerate ordered quad`);
  return value.map(point=>[...point]);
}
function frameRate(value){
  if(typeof value==='number')return positive(value,'avgFrameRate');
  if(typeof value==='string'){
    const match=/^(\d+(?:\.\d+)?)(?:\/(\d+(?:\.\d+)?))?$/.exec(value);
    if(match)return positive(Number(match[1])/(match[2]===undefined?1:Number(match[2])),'avgFrameRate');
  }
  throw new TypeError('avgFrameRate must be a positive number or rational string');
}
const cloneFrame=frame=>({...frame,homography:frame.homography?.map(row=>[...row])??null,quad:frame.quad?.map(point=>[...point])??null});

/**
 * Hold real tracked observations on [frame.time, next.time). Never blend through loss.
 * A new sampler snapshots input data; arbitrary and reverse seeks have identical output.
 */
export function createTrackSampler(track){
  if(!track||track.schemaVersion!==1)throw new TypeError('Expected tracking schemaVersion 1');
  const {source}=track;
  if(!source)throw new TypeError('Track source metadata is required');
  positive(source.width,'source.width');positive(source.height,'source.height');
  if(!Number.isSafeInteger(source.frameCount)||source.frameCount<1)throw new RangeError('source.frameCount must be a positive integer');
  const fps=frameRate(source.avgFrameRate);
  if(track.initialQuad!==undefined){
    const initial=quad(track.initialQuad,'initialQuad');
    if(initial.some(([x,y])=>x<0||y<0||x>source.width||y>source.height))throw new RangeError('initialQuad must lie inside the original source dimensions');
  }
  if(!Array.isArray(track.frames)||track.frames.length===0)throw new TypeError('Track needs at least one observed frame');
  let previousTime=-Infinity,previousIndex=-1;
  const frames=track.frames.map(frame=>{
    finite(frame.time,'frame.time');finite(frame.sourceTime,'frame.sourceTime');
    if(frame.time<0||frame.time<=previousTime)throw new RangeError('Frame times must be nonnegative and strictly increasing');
    if(!Number.isSafeInteger(frame.index)||frame.index<=previousIndex||frame.index>=source.frameCount)throw new RangeError('Frame indices must increase within source.frameCount');
    previousTime=frame.time;previousIndex=frame.index;
    if(!['initialized','tracked','lost'].includes(frame.status))throw new TypeError('Unknown tracking status');
    if(!Number.isFinite(frame.confidence)||frame.confidence<0||frame.confidence>1)throw new RangeError('Frame confidence must be in [0, 1]');
    for(const key of ['features','inliers'])if(!Number.isSafeInteger(frame[key])||frame[key]<0)throw new RangeError(`${key} must be a nonnegative integer`);
    const snapshot=frame.status==='lost'?{...frame,homography:null,quad:null}:cloneFrame(frame);
    if(frame.status==='lost'){
      // A stale matrix attached to a lost frame must never be displayed.
      snapshot.homography=null;snapshot.quad=null;
    }else{
      matrix(frame.homography);
      snapshot.quad=quad(frame.quad,'tracked quad');
    }
    return snapshot;
  });
  // duration is normalized to the clip timeline, not an absolute source timestamp.
  const duration=source.duration;
  if(duration!==undefined&&duration!==null)positive(duration,'source.duration');
  const end=duration>frames.at(-1).time?duration:frames.at(-1).time+1/fps;
  return time=>{
    finite(time,'sample time');
    if(time<frames[0].time)return {visible:false,frame:null,reason:'before track coverage'};
    if(time>=end)return {visible:false,frame:null,reason:'after track coverage'};
    let low=0,high=frames.length;
    while(low+1<high){const mid=Math.floor((low+high)/2);if(frames[mid].time<=time)low=mid;else high=mid;}
    const frame=cloneFrame(frames[low]);
    return frame.status==='lost'?{visible:false,frame,reason:frame.reason||'tracking lost'}:{visible:true,frame};
  };
}
