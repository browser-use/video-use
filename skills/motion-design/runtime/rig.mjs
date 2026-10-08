/** Two-bone inverse kinematics for arbitrary 2D limbs, rods or linked mechanisms. */
export function twoBoneIK({root,target,upper,lower,bend=1}) {
  for(const p of [root,target])if(!p || ![p.x,p.y].every(Number.isFinite))throw new TypeError('root and target must be finite points');
  if(![upper,lower].every(n=>Number.isFinite(n)&&n>0))throw new RangeError('bone lengths must be positive');
  if(bend!==1 && bend!==-1)throw new RangeError('bend must be +1 or -1');
  const dx=target.x-root.x,dy=target.y-root.y,requested=Math.hypot(dx,dy);
  const distance=Math.min(upper+lower,Math.max(Math.abs(upper-lower),requested));
  const direction=requested===0?0:Math.atan2(dy,dx);
  // Equal-length folded chains have a well-defined chosen elbow even at the root.
  const offset=distance===0?Math.PI/2:Math.acos(Math.max(-1,Math.min(1,(upper*upper+distance*distance-lower*lower)/(2*upper*distance))));
  const angle=direction+bend*offset;
  const elbow={x:root.x+Math.cos(angle)*upper,y:root.y+Math.sin(angle)*upper};
  const end={x:root.x+Math.cos(direction)*distance,y:root.y+Math.sin(direction)*distance};
  return {elbow,end,upperAngle:angle,lowerAngle:Math.atan2(end.y-elbow.y,end.x-elbow.x),reachable:requested>=Math.abs(upper-lower)&&requested<=upper+lower};
}
