import { chromium } from 'playwright';
const b = await chromium.launch();
const p = await b.newPage({ viewport:{width:1024,height:1536}, deviceScaleFactor:1, colorScheme:'light' });
await p.addInitScript(()=>{try{localStorage.setItem('usr.theme','light')}catch{}});
await p.goto('http://localhost:5311/market?newui=1',{waitUntil:'domcontentloaded'});
await p.waitForTimeout(3500);
console.log(await p.evaluate(()=>{
  const root=document.querySelector('.nmo .page');
  const out=[];
  const walk=(el,depth)=>{ [...el.children].forEach(c=>{
    const r=c.getBoundingClientRect();
    const contents=getComputedStyle(c).display==='contents';
    // A display:contents element has a ZERO rect, so filtering on height before recursing hid
    // every panel inside .mo-grid - which is exactly the band being measured.
    if(contents){ out.push(`${' '.repeat(depth*2)}[${(c.className||'').toString().slice(0,24)} = contents]`); walk(c,depth+1); return; }
    if(r.height<2) return;
    out.push(`${' '.repeat(depth*2)}${c.tagName.toLowerCase()}.${(c.className||'').toString().slice(0,32)} y=${Math.round(r.y-67)} h=${Math.round(r.height)} x=${Math.round(r.x-247)} w=${Math.round(r.width)}`);
  });};
  walk(root,0);
  return out.join('\n');
}));
await b.close();
