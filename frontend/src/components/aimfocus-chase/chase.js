export function mountAIMfocusChase(root) {
 if (!root) throw new Error("AIMfocus chase requires a root element");
 const ctx=root.querySelector('canvas').getContext('2d');
 const reduced=matchMedia('(prefers-reduced-motion: reduce)');
 const run=new Image(),turn=new Image(),nut=new Image();
 let rb=[],tb=[],nb,ready=0,t=0,last=0,raf=0,destroyed=false;
 const leg=4000,pivot=1000,half=leg+pivot;
 const smooth=x=>x*x*(3-2*x);
 function bounds(img,lo,hi){
  const c=document.createElement('canvas');c.width=img.width;c.height=img.height;
  const g=c.getContext('2d');g.drawImage(img,0,0);const pixels=g.getImageData(0,0,c.width,c.height).data;
  const start=Math.floor(lo*c.width),end=Math.floor(hi*c.width);
  let x0=end,x1=start,y0=c.height,y1=0;
  for(let y=0;y<c.height;y++)for(let x=start;x<end;x++)if(pixels[(y*c.width+x)*4+3]>65){x0=Math.min(x0,x);x1=Math.max(x1,x);y0=Math.min(y0,y);y1=Math.max(y1,y);}
  return {x:x0,y:y0,w:x1-x0+1,h:y1-y0+1};
 }
 function sprite(img,b,x,ground,h,mirror=1,alpha=1,tilt=0){
  const k=h/b.h;ctx.save();ctx.translate(x,ground);ctx.rotate(tilt);ctx.scale(mirror,1);ctx.globalAlpha=alpha;
  ctx.drawImage(img,b.x,b.y,b.w,b.h,-b.w*k/2,-h,b.w*k,h);ctx.restore();
 }
 function runFrame(x,y,d,time,speed){
  const g=time/145,seq=[0,1,2,1],i=Math.floor(g)%4,f=g%1;
  const blend=smooth(Math.max(0,(f-.72)/.28));
  const hop=Math.sin(g*Math.PI)*1.6*speed;
  sprite(run,rb[seq[i]],x,y+hop,60,d,1-blend);
  if(blend>0)sprite(run,rb[seq[(i+1)%4]],x,y+hop,60,d,blend);
 }
 function turnFrame(x,y,d,u){
  const frame=smooth(u)*6,index=Math.min(5,Math.floor(frame)),blend=smooth(frame-index);
  const poses=d===1?[-1,0,1,2,3,4,-2]:[-2,4,3,2,1,0,-1];
  function pose(code,opacity){
   if(opacity<=0)return;
   if(code<0)sprite(run,rb[0],x,y,60,code===-1?1:-1,opacity);
   else sprite(turn,tb[code],x,y,60,1,opacity);
  }
  pose(poses[index],1-blend);pose(poses[index+1],blend);
 }
 function draw(){
  ctx.setTransform(2,0,0,2,0,0);ctx.clearRect(0,0,700,170);if(ready<3)return;
  const cycle=t%(half*2),d=cycle<half?1:-1,local=cycle%half;
  let x,nx,ny;
  if(local<leg){
   const u=local/leg,e=smooth(u);x=d===1?110+480*e:590-480*e;
   const speed=4*u*(1-u);
   runFrame(x,119,d,t,speed);
   nx=x+d*68;ny=117-Math.abs(Math.sin(t/185))*4*speed;
  }else{
   const u=(local-leg)/pivot;
   x=(d===1?590:110)+d*10*Math.sin(Math.PI*u);
   turnFrame(x,119-3*Math.sin(Math.PI*u),d,u);
   const angle=Math.PI*smooth(u);
   nx=x+d*68*Math.cos(angle);ny=117-64*Math.sin(angle);
  }
  sprite(nut,nb,nx,ny,19,1,1,Math.sin(t/230)*.12);
 }
 function tick(now){if(destroyed||!root.isConnected)return;if(!reduced.matches&&!document.hidden)t+=last?Math.min(now-last,40):0;last=now;draw();raf=requestAnimationFrame(tick);}
 run.onload=()=>{rb=[[0,.281],[.281,.531],[.531,.779]].map(r=>bounds(run,...r));ready++;};
 turn.onload=()=>{tb=[[0,.255],[.255,.443],[.443,.568],[.568,.75],[.75,1]].map(r=>bounds(turn,...r));ready++;};
 nut.onload=()=>{nb=bounds(nut,.81,1);ready++;};
 run.src=new URL('./squirrel-run.webp', import.meta.url).href;turn.src=new URL('./squirrel-turn.webp', import.meta.url).href;nut.src=new URL('./acorn-atlas.webp', import.meta.url).href;
 raf=requestAnimationFrame(tick);
 return () => {
  destroyed=true;
  cancelAnimationFrame(raf);
  run.onload=turn.onload=nut.onload=null;
  ctx.clearRect(0,0,700,170);
 };
}
