import { chromium } from 'playwright';
const b = await chromium.launch();
const p = await b.newPage({ viewport:{width:1024,height:1536}, deviceScaleFactor:1, colorScheme:'light' });
await p.addInitScript(()=>{try{localStorage.setItem('usr.theme','light')}catch{}});
await p.goto('http://localhost:5311/market?newui=1',{waitUntil:'domcontentloaded'});
await p.waitForTimeout(3000);
console.log(await p.evaluate(()=>{
  const s=document.querySelector('.mo-search'); const r=s.getBoundingClientRect();
  const path=[]; let n=s; while(n && n!==document.body){ path.push(`${n.tagName.toLowerCase()}.${(n.className||'').toString().slice(0,24)}`); n=n.parentElement; }
  const inp=s.querySelector('input'); const ir=inp.getBoundingClientRect();
  const go=s.querySelector('.mo-search-go'); const gr=go.getBoundingClientRect();
  return `search x=${Math.round(r.x)} y=${Math.round(r.y)} w=${Math.round(r.width)} h=${Math.round(r.height)}\n`
   +`input w=${Math.round(ir.width)}  go x=${Math.round(gr.x)} y=${Math.round(gr.y)}\n`+path.join(' < ');
}));
await b.close();
