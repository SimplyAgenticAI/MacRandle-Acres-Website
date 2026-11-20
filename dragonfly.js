/* MacRandle Acres dragonfly, shared by the homepage and /design.
   Include with <script src="/dragonfly.js" data-target="CSS selector of the words to land on" defer></script> */
var CSS=".mra-df-hit{position:absolute;left:0;top:0;width:76px;height:56px;margin:0;padding:0;border:0;background:transparent;border-radius:28px;cursor:pointer;z-index:41;-webkit-tap-highlight-color:transparent}\n.mra-df-hit:focus-visible{outline:2px solid var(--gold,#c79a3b);outline-offset:2px}\n.mra-df-tip{position:absolute;z-index:42;width:264px;max-width:calc(100vw - 24px);background:#fff;border:1px solid rgba(35,79,61,.14);border-radius:18px;box-shadow:0 14px 34px rgba(26,59,45,.2);padding:11px 32px 11px 14px;text-align:left;font-family:inherit;opacity:0;transform:translateY(-4px) scale(.94);transform-origin:var(--tail,80%) 0;transition:opacity .18s ease,transform .22s cubic-bezier(.2,.9,.3,1.3);pointer-events:none}\n.mra-df-tip:before{content:\"\";position:absolute;top:-7px;left:var(--tail,80%);width:12px;height:12px;margin-left:-6px;background:#fff;border-top:1px solid rgba(35,79,61,.14);border-left:1px solid rgba(35,79,61,.14);transform:rotate(45deg);border-radius:3px 0 0 0}\n.mra-df-tip.side{transform-origin:0 var(--ty,80%)}\n.mra-df-tip.side:before{top:var(--ty,80%);left:-7px;margin:-6px 0 0 0;border-top:0;border-bottom:1px solid rgba(35,79,61,.14);border-radius:0 0 0 3px}\n.mra-df-tip.on{opacity:1;transform:none;pointer-events:auto}\n.mra-df-tip .k{display:inline-block;font-size:10px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:var(--gold-d,#a97f2a);background:rgba(199,154,59,.12);border-radius:999px;padding:2px 8px;margin:0 0 6px}\n.mra-df-tip p{margin:0;font-size:14px;line-height:1.45;color:var(--ink,#2D2D2D)}\n.mra-df-tip .n{margin-top:6px;font-size:11px;color:var(--ink3,#8a8f88)}\n.mra-df-tip .x{position:absolute;top:6px;right:8px;border:0;background:none;font-size:17px;line-height:1;color:var(--ink3,#8a8f88);cursor:pointer;padding:4px}\n@media(prefers-reduced-motion:reduce){.mra-df-tip{transition:none}}";
/* The MacRandle Acres dragonfly. Same family as the Tallgrass meadow dragonfly
   (Tallgrass static/js/field.js): four wings, segmented green tail, gold eyes.
   It glides in, hovers, darts onto "Remove Friction." and perches there.
   Click or tap it for a quick marketing, branding or networking tip. */
(function(){
 var me=document.currentScript, sel=(me&&me.getAttribute('data-target'))||'.hero h1 .hl .accent-text';
 var h=document.querySelector(sel)||document.querySelector('h1');
 if(!h||!window.requestAnimationFrame) return;
 var st=document.createElement('style'); st.textContent=CSS; document.head.appendChild(st);
 var REDUCE=window.matchMedia&&matchMedia('(prefers-reduced-motion:reduce)').matches;
 var SIZE=window.innerWidth<620?1.05:1.5, BOX=Math.round(100*SIZE);
 var TIPS=[
  ['Marketing','Answer the question your customer is already asking. A post that solves one real problem gets saved and shared, and that is what helps it travel.'],
  ['Branding','Your profile photo, cover and bio are your storefront. One clear line about who you help and how beats a clever slogan.'],
  ['Networking','Give before you ask. Share someone else\'s post, send a referral or make an intro, and people remember who helped first.'],
  ['Marketing','Post less, but make each one count. One genuinely useful post does more than five filler posts.'],
  ['Branding','Pick two brand colors and one font and use them everywhere. People recognize you before they read your name.'],
  ['Networking','Follow up within a day of meeting someone, and mention one thing you talked about. That is how a handshake becomes a relationship.'],
  ['Marketing','Show up in your local community groups as a neighbor first. Helpful comments build more trust than another ad.'],
  ['Branding','Consistency builds trust faster than creativity. Show up on a steady schedule your audience can count on.'],
  ['Networking','Comment on other business owners\' posts with something real to add. It puts your name in front of their audience, free.'],
  ['Marketing','Remove friction for your customer: one clear button and one clear next step. Every extra choice is a chance to lose them.'],
  ['Branding','Tell people why you do what you do. A real story is the one thing a competitor can\'t copy.'],
  ['Networking','Keep a short list of people you can refer work to. Being the person who knows someone makes you the first call.'],
  ['Marketing','Reply to comments quickly and like a person. Conversations are what turn a post into a customer.']
 ];
 var dpr=Math.min(window.devicePixelRatio||1,2);
 var cv=document.createElement('canvas'); cv.className='mra-dragonfly'; cv.setAttribute('aria-hidden','true');
 cv.width=BOX*dpr; cv.height=BOX*dpr;
 cv.style.cssText='position:absolute;left:0;top:0;width:'+BOX+'px;height:'+BOX+'px;pointer-events:none;z-index:40;will-change:transform;opacity:0';
 var hit=document.createElement('button'); hit.type='button'; hit.className='mra-df-hit';
 hit.setAttribute('aria-label','Dragonfly: show a quick marketing, branding or networking tip'); hit.title='Click me for a tip'; hit.style.display='none';
 var tip=document.createElement('div'); tip.className='mra-df-tip'; tip.setAttribute('role','status'); tip.setAttribute('aria-live','polite');
 tip.innerHTML='<button type="button" class="x" aria-label="Close tip">&times;</button><div class="k"></div><p></p><div class="n">Tap me again for another.</div>';
 document.body.appendChild(cv); document.body.appendChild(hit); document.body.appendChild(tip);
 var ctx=cv.getContext('2d');

 function target(){var r=h.getBoundingClientRect();return {x:r.right+window.scrollX-20*SIZE, y:r.top+window.scrollY+r.height*0.08-6*SIZE};}

 /* pose: fore/hind wing angles (radians off the resting sweep), glow 0..1 */
 function draw(fore,hind,glow){
  ctx.setTransform(dpr,0,0,dpr,0,0); ctx.clearRect(0,0,BOX,BOX);
  ctx.save(); ctx.translate(BOX/2,BOX/2);
  if(glow>0){var g0=ctx.createRadialGradient(0,0,2,0,0,40*SIZE);g0.addColorStop(0,'rgba(224,184,98,'+(0.22*glow).toFixed(3)+')');g0.addColorStop(1,'rgba(224,184,98,0)');ctx.fillStyle=g0;ctx.beginPath();ctx.arc(0,0,40*SIZE,0,Math.PI*2);ctx.fill();}
  ctx.scale(SIZE,SIZE);
  var wings=[{at:-1.5,span:18,width:4.1,side:-1,a:hind},{at:-1.5,span:18,width:4.1,side:1,a:hind},{at:3.5,span:21,width:3.4,side:-1,a:fore},{at:3.5,span:21,width:3.4,side:1,a:fore}];
  for(var w=0;w<4;w++){var g=wings[w];ctx.save();ctx.translate(g.at,0);ctx.rotate(g.side*(1.36+g.a));
   var wf=ctx.createLinearGradient(0,0,0,g.span);wf.addColorStop(0,'rgba(52,170,120,0.55)');wf.addColorStop(0.35,'rgba(150,225,195,0.42)');wf.addColorStop(1,'rgba(230,248,240,0.28)');
   ctx.beginPath();ctx.ellipse(0,g.span*0.5,g.width,g.span*0.5,0,0,Math.PI*2);ctx.fillStyle=wf;ctx.fill();
   ctx.lineWidth=0.6;ctx.strokeStyle='rgba(26,80,58,0.55)';ctx.stroke();
   ctx.lineWidth=0.35;ctx.strokeStyle='rgba(26,80,58,0.35)';
   ctx.beginPath();ctx.moveTo(-g.width*0.45,1);ctx.lineTo(-g.width*0.2,g.span*0.94);ctx.moveTo(g.width*0.1,1.5);ctx.quadraticCurveTo(g.width*0.55,g.span*0.5,g.width*0.15,g.span*0.9);ctx.stroke();
   ctx.beginPath();ctx.ellipse(-g.width*0.22,g.span*0.84,0.95,1.9,0,0,Math.PI*2);ctx.fillStyle='rgba(199,154,59,0.9)';ctx.fill();
   ctx.restore();}
  var tail=ctx.createLinearGradient(-31,0,0,0);tail.addColorStop(0,'rgba(26,90,62,0.95)');tail.addColorStop(1,'rgba(74,200,150,0.98)');
  ctx.beginPath();ctx.moveTo(-1,-2.2);ctx.quadraticCurveTo(-16,-1.6,-29,-0.6);ctx.quadraticCurveTo(-31.2,0,-29,0.6);ctx.quadraticCurveTo(-16,1.6,-1,2.2);ctx.closePath();ctx.fillStyle=tail;ctx.fill();
  for(var s=1;s<=6;s++){var sx=-2.5-s*4.1,hf=2.0*(1-s/7.5);ctx.strokeStyle=s%2?'rgba(6,40,27,0.45)':'rgba(224,184,98,0.55)';ctx.lineWidth=s%2?0.55:0.45;ctx.beginPath();ctx.moveTo(sx,-hf);ctx.lineTo(sx,hf);ctx.stroke();}
  ctx.beginPath();ctx.ellipse(-29.6,0,1.5,0.8,0,0,Math.PI*2);ctx.fillStyle='rgba(199,154,59,0.95)';ctx.fill();
  ctx.beginPath();ctx.ellipse(1.5,0,5.4,3.3,0,0,Math.PI*2);ctx.fillStyle='rgba(40,150,105,0.97)';ctx.fill();
  ctx.beginPath();ctx.moveTo(-2.8,0);ctx.lineTo(5.6,0);ctx.lineWidth=0.9;ctx.strokeStyle='rgba(224,184,98,0.85)';ctx.stroke();
  ctx.beginPath();ctx.ellipse(7.6,0,2.6,2.9,0,0,Math.PI*2);ctx.fillStyle='rgba(74,200,150,0.97)';ctx.fill();
  ctx.fillStyle='rgba(212,168,72,0.97)';ctx.beginPath();ctx.ellipse(8.6,-1.9,2.15,2.35,0.3,0,Math.PI*2);ctx.fill();ctx.beginPath();ctx.ellipse(8.6,1.9,2.15,2.35,-0.3,0,Math.PI*2);ctx.fill();
  ctx.fillStyle='rgba(255,252,240,0.9)';ctx.beginPath();ctx.arc(9.5,-2.4,0.6,0,Math.PI*2);ctx.fill();ctx.beginPath();ctx.arc(9.5,2.4,0.6,0,Math.PI*2);ctx.fill();
  ctx.restore();
 }
 var REST=-0.12, cur={x:0,y:0,a:REST};
 function place(x,y,a){cur.x=x;cur.y=y;cur.a=a;cv.style.transform='translate('+(x-BOX/2)+'px,'+(y-BOX/2)+'px) rotate('+a+'rad)';hit.style.transform='translate('+(x-38)+'px,'+(y-28)+'px)';}
 function lerp(a,b,k){return a+(b-a)*k;}
 function ease(k){return k<0?0:k>1?1:1-Math.pow(1-k,3);}
 function bez(p0,p1,p2,p3,t){var u=1-t;return u*u*u*p0+3*u*u*t*p1+3*u*t*t*p2+t*t*t*p3;}

 /* Tips */
 var n=Math.floor(Math.random()*TIPS.length), clicked=false, hopAt=-1e9;
 function showTip(){
  n=(n+1)%TIPS.length; tip.querySelector('.k').textContent=TIPS[n][0]+' tip'; tip.querySelector('p').textContent=TIPS[n][1];
  var t=target(), W=Math.min(264,window.innerWidth-24), left=Math.max(12,Math.min(t.x-W+48,document.documentElement.clientWidth-W-12));
  var H=tip.offsetHeight||120, cw=document.documentElement.clientWidth, rl=t.x+26*SIZE;
  if(rl+W<=cw-12){ /* room on the right: sit above and to the right of the dragonfly, in the open space */
   var top=Math.max(8,t.y-H+4*SIZE), ty=Math.max(18,Math.min(H-18,t.y+6*SIZE-top));
   tip.classList.add('side'); tip.style.setProperty('--ty',ty+'px'); tip.style.left=rl+'px'; tip.style.top=top+'px';
  } else { /* narrow screens: fall back to just below it */
   var tail=Math.max(20,Math.min(W-20,t.x-left));
   tip.classList.remove('side'); tip.style.setProperty('--tail',tail+'px'); tip.style.left=left+'px'; tip.style.top=(t.y+24*SIZE)+'px';
  }
  tip.classList.remove('on'); void tip.offsetWidth; tip.classList.add('on'); clicked=true;
 }
 function hideTip(){tip.classList.remove('on');}
 hit.addEventListener('click',function(e){e.stopPropagation();hopAt=performance.now();showTip();if(REDUCE)render(performance.now());});
 tip.querySelector('.x').addEventListener('click',function(e){e.stopPropagation();hideTip();hit.focus();});
 tip.addEventListener('click',function(e){e.stopPropagation();});
 document.addEventListener('click',hideTip);
 document.addEventListener('keydown',function(e){if(e.key==='Escape')hideTip();});
 window.addEventListener('resize',function(){if(tip.classList.contains('on')){n=(n-1+TIPS.length)%TIPS.length;showTip();}});

 function render(now){var t=target();place(t.x,t.y,REST);draw(0.02,0.06,clicked?0:0.6);}
 if(REDUCE){cv.style.opacity='1';hit.style.display='block';render(0);window.addEventListener('resize',function(){render(0);});return;}

 /* Flight: glide in (0-1.5s), hover and turn (1.5-2.5s), dart and land (2.5-3.1s), then perch. */
 var start=null, DELAY=1300, HZ=2.2;
 function frame(now){
  if(start===null) start=now+DELAY;
  var ms=now-start, t=target(), hx=t.x-110*SIZE, hy=t.y-40*SIZE;
  if(ms<0){requestAnimationFrame(frame);return;}
  cv.style.opacity='1';
  var ph=now/1000*HZ*Math.PI*2, flying=ms<3100, fore, hind, x, y, a, glow=0;
  if(ms<1500){
   var k=ease(ms/1500), sx=-BOX, sy=hy-150;
   x=bez(sx,hx*0.3,hx*0.7,hx,k); y=bez(sy,sy-30,hy+90,hy,k);
   var k2=ease((ms+16)/1500), x2=bez(sx,hx*0.3,hx*0.7,hx,k2), y2=bez(sy,sy-30,hy+90,hy,k2);
   a=Math.atan2(y2-y,x2-x)*0.6;
  } else if(ms<2500){
   var q=(ms-1500)/1000; x=hx+Math.sin(q*Math.PI)*6; y=hy+Math.sin(q*Math.PI*2)*3;
   a=lerp(0.1,Math.atan2(t.y-hy,t.x-hx)*0.5,ease(q));
  } else if(ms<3100){
   var d=ease((ms-2500)/600); x=lerp(hx,t.x,d); y=lerp(hy,t.y,d)-Math.sin(d*Math.PI)*14*SIZE;
   a=lerp(Math.atan2(t.y-hy,t.x-hx)*0.5,REST,d);
  }
  if(flying){
   var amp=ms>2900?lerp(0.3,0.05,(ms-2900)/200):0.3;
   fore=Math.sin(ph)*amp; hind=Math.sin(ph-Math.PI*0.45)*amp;
  } else {
   hit.style.display='block';
   var p=(ms-3100)/1000, hop=(now-hopAt)/1000;
   x=t.x; y=t.y+Math.sin(p*1.1)*1.1; a=REST+Math.sin(p*0.8)*0.025;
   /* a slow stretch of the wings every few seconds, the way a resting dragonfly shifts */
   var cyc=p%7, flex=cyc<1.2?Math.sin(cyc/1.2*Math.PI)*0.16:0;
   fore=0.02+flex; hind=0.06+flex*0.8;
   if(hop<0.9){var hk=hop/0.9;y-=Math.sin(hk*Math.PI)*12*SIZE;fore=Math.sin(ph*1.2)*0.28*(1-hk);hind=Math.sin(ph*1.2-1.4)*0.28*(1-hk);}
   if(!clicked) glow=0.35+0.35*Math.sin(p*1.6);
  }
  place(x,y,a); draw(fore,hind,glow); requestAnimationFrame(frame);
 }
 requestAnimationFrame(frame);
})();
