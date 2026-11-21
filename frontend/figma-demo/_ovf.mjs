import { chromium } from 'playwright';
const b = await chromium.launch();
const p = await b.newPage({ viewport:{width:1024,height:1536}, deviceScaleFactor:1, colorScheme:'light' });
await p.addInitScript(()=>{try{localStorage.setItem('usr.theme','light')}catch{}});
await p.goto('http://localhost:5311/market?newui=1',{waitUntil:'domcontentloaded'});
await p.waitForTimeout(3000);
console.log(await p.evaluate(()=>{
  const pg=document.querySelector('.nmo .page');
  const cs=getComputedStyle(pg);
  const out=[`page w=${Math.round(pg.getBoundingClientRect().width)} scrollW=${pg.scrollWidth} cols=${cs.gridTemplateColumns}`];
  const widest=[...pg.querySelectorAll('*')].map(e=>({e,w:e.getBoundingClientRect().right})).sort((a,b)=>b.w-a.w).slice(0,4);
  widest.forEach(({e,w})=>out.push(`  right=${Math.round(w)} <${e.tagName.toLowerCase()}.${(e.className||'').toString().slice(0,30)}>`));
  return out.join('\n');
}));
await b.close();
