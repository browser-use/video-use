/** Real browser forward/backward-seek checks; optional provisioned Playwright. */
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const modules = process.env.VIDEO_USE_MOTION_NODE_MODULES;

test('UI, text and geometry render and repeated random seeks preserve output', {skip:!modules}, async () => {
  const {chromium} = require(path.join(modules,'playwright'));
  const browser = await chromium.launch({headless:true,args:['--no-sandbox'],
    executablePath:process.env.VIDEO_USE_CHROMIUM_EXECUTABLE || undefined});
  try {
    const page = await browser.newPage({viewport:{width:1280,height:720},deviceScaleFactor:1});
    const errors=[];
    page.on('pageerror',error=>errors.push(error.message));
    await page.setContent('<html><body style="margin:0;background:#101319;color:white"><main id="root" style="width:1280px;height:720px;position:relative"></main></body></html>');
    await page.addStyleTag({path:path.resolve(__dirname,'../skills/motion-design/assets/motion-kit.css')});
    await page.addScriptTag({path:path.join(modules,'gsap/dist/gsap.min.js')});
    await page.addScriptTag({path:path.resolve(__dirname,'../skills/motion-design/assets/motion-kit.js')});
    await page.evaluate(()=>{
      const M=MotionKit,root=document.querySelector('#root'),tl=gsap.timeline({paused:true});
      const terminal=M.terminal({width:540,height:270,lines:['$ compile 👩🏽‍💻 safely','✓ complete']});
      Object.assign(terminal.root.style,{position:'absolute',left:'40px',top:'40px'});root.append(terminal.root);
      const prompt=M.promptBar({text:'Turn an idea into a film',width:900,action:'Create'});
      Object.assign(prompt.root.style,{position:'absolute',left:'40px',top:'560px'});root.append(prompt.root);
      const menu=M.menu({items:['Review','Export'],width:220});
      Object.assign(menu.root.style,{position:'absolute',left:'950px',top:'440px'});root.append(menu.root);
      const sphere=M.orb({size:140}),halo=M.halo({size:190});
      Object.assign(sphere.style,{position:'absolute',left:'650px',top:'70px'});root.append(sphere);
      Object.assign(halo.root.style,{position:'absolute',left:'890px',top:'50px'});root.append(halo.root);
      const svg=M.vector('svg',{width:500,height:180,viewBox:'0 0 500 180'});
      Object.assign(svg.style,{position:'absolute',left:'70px',top:'350px'});
      const curve=M.vector('path',{d:M.curvePath(x=>.5+.3*Math.sin(x*8),{width:500,height:180}),fill:'none',stroke:'#9fe3af','stroke-width':3});
      svg.append(curve);root.append(svg);
      const pointer=M.cursor();root.append(pointer.root);
      M.typeText(tl,terminal.rows[0],{at:.3,duration:1.2});
      M.revealText(tl,terminal.rows[1],{at:1.8});
      M.revealText(tl,prompt.input,{at:.2});
      M.pointerMove(tl,pointer.root,{at:.5,duration:1,from:[850,350],to:[850,600],click:true});
      M.drawPath(tl,curve,{at:.2,duration:2});
      const badge=M.element('div','test-badge','', {position:'absolute',left:'600px',top:'350px',background:'#c6dfcb'});
      root.append(badge);
      M.morphBox(tl,badge,{at:1,duration:.6,from:{width:20,height:20,radius:10},to:{width:180,height:48,radius:14}});
      const rider=M.vector('circle',{r:5,fill:'#fff'});svg.append(rider);
      M.followPath(tl,rider,curve,{at:.2,duration:2,samples:24});
      const masked=M.maskedText({text:'A useful gesture',fontSize:24,width:260});
      Object.assign(masked.root.style,{position:'absolute',left:'600px',top:'460px'});root.append(masked.root);
      masked.content.replaceChildren(M.element('em','','A useful'),document.createElement('br'),document.createTextNode('gesture'));
      M.revealText(tl,masked.content,{at:.5,duration:.4});
      M.camera(tl,masked.content,{at:.8,duration:.6,from:{x:0,y:50,scale:1},to:{x:0,y:0,scale:1}});
      M.register('test',tl,4);
      window.testTimeline=tl;
      window.typingRow=terminal.rows[0];
    });
    await page.evaluate(()=>document.fonts.ready);
    const rich=await page.evaluate(()=>{
      const node=document.querySelector('.motion-masked-text');
      const parts=MotionKit.splitText(node);
      return {em:node.querySelector('em').textContent,breaks:node.querySelectorAll('br').length,
        parts:parts.length,nested:node.querySelectorAll('.motion-text-part .motion-text-part').length};
    });
    assert.deepEqual(rich,{em:'A useful',breaks:1,parts:3,nested:0});
    const shell=await page.evaluate(()=>{
      const panel=document.querySelector('.motion-window'),body=panel.querySelector('.motion-window-body');
      return {bodyBottom:body.getBoundingClientRect().bottom,
        contentBottom:panel.getBoundingClientRect().bottom-parseFloat(getComputedStyle(panel).borderBottomWidth)};
    });
    assert.ok(shell.bodyBottom<=shell.contentBottom,'window chrome must include its border in its declared height');
    async function capture(t) {
      await page.evaluate(t=>{testTimeline.seek(t,false);gsap.ticker.sleep();},t);
      await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
      return page.screenshot({type:'png'});
    }
    const state=()=>page.evaluate(()=>[...document.querySelectorAll('#root *')].map(n=>[n.tagName,n.className.baseVal??n.className,n.getAttribute('style')]));
    await capture(0);
    const zeroState=await state();
    const early=await capture(.6);
    const initialState=await state();
    const later=await capture(2.8);
    assert.notDeepEqual(early,later,'scene must actually change');
    const widths=[];
    for(const t of [0,3,.6,1.2,2.8,.6]) {
      const pixels=await capture(t);
      if(t===0) assert.deepEqual(await state(),zeroState,'return to frame zero changed initial geometry');
      if(t===.6) {
        assert.deepEqual(await state(),initialState,'random seek changed DOM styles');
        if(process.env.VIDEO_USE_MOTION_BROWSER_EVIDENCE) {
          fs.mkdirSync(process.env.VIDEO_USE_MOTION_BROWSER_EVIDENCE,{recursive:true});
          fs.writeFileSync(path.join(process.env.VIDEO_USE_MOTION_BROWSER_EVIDENCE,'first.png'),early);
          fs.writeFileSync(path.join(process.env.VIDEO_USE_MOTION_BROWSER_EVIDENCE,'repeated.png'),pixels);
        }
        const difference=await page.evaluate(async ([a,b])=>{
          const canvas=document.createElement('canvas');canvas.width=1280;canvas.height=720;
          const ctx=canvas.getContext('2d',{willReadFrequently:true});
          const read=async value=>{
            const bitmap=await createImageBitmap(await (await fetch('data:image/png;base64,'+value)).blob());
            ctx.clearRect(0,0,1280,720);ctx.drawImage(bitmap,0,0);bitmap.close();
            return ctx.getImageData(0,0,1280,720).data;
          };
          const first=await read(a),second=await read(b);let count=0,max=0;
          for(let i=0;i<first.length;i+=4) {
            const delta=Math.max(...[0,1,2].map(c=>Math.abs(first[i+c]-second[i+c])));
            if(delta) count++;max=Math.max(max,delta);
          }
          return {count,max};
        },[early.toString('base64'),pixels.toString('base64')]);
        // Layer promotion can change a few rounded-edge pixels or one color
        // level across a small edge on macOS. Keep exact DOM equality and
        // bounded alternatives rather than allowing arbitrary image drift.
        const edgeNoise=difference.count<=8 && difference.max<=32;
        const quantizationNoise=difference.count<=128 && difference.max<=1;
        assert.ok(edgeNoise || quantizationNoise,JSON.stringify(difference));
      }
      widths.push(await page.evaluate(()=>typingRow.getBoundingClientRect().width));
    }
    assert.equal(new Set(widths).size,1,'typing must not recenter or relayout');
    assert.deepEqual(errors,[]);
  } finally {await browser.close();}
});
