import { chromium } from 'playwright';
const b = await chromium.launch();
const p = await b.newPage({ viewport:{width:1440,height:900}, deviceScaleFactor:1, colorScheme:'dark' });
await p.addInitScript(()=>{try{localStorage.setItem('usr.theme','dark')}catch{}});
await p.goto('http://localhost:5311/post-earnings-forward?newui=1',{waitUntil:'domcontentloaded'});
await p.waitForTimeout(3000);
console.log(await p.evaluate(()=>{
  const out=[];
  // find elements whose colour is near-white sitting on a near-white background
  const near=(c,t)=>{const m=c.match(/\d+/g); if(!m) return false; const [r,g,bl]=m.map(Number); return (r+g+bl)/3 > t;};
  document.querySelectorAll('*').forEach(e=>{
    const cs=getComputedStyle(e);
    if(!e.textContent.trim() || e.children.length) return;
    let bg='rgba(0,0,0,0)', n=e;
    while(n && bg==='rgba(0, 0, 0, 0)'){ bg=getComputedStyle(n).backgroundColor; n=n.parentElement; }
    if(near(cs.color,200) && near(bg,200)) out.push(`<${e.tagName.toLowerCase()}.${(e.className||'').toString().slice(0,28)}> "${e.textContent.trim().slice(0,16)}" color=${cs.color} on ${bg}`);
  });
  return out.slice(0,12).join('\n')||'none';
}));
await b.close();
