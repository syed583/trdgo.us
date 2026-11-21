import { chromium } from 'playwright';
const b = await chromium.launch();
const p = await b.newPage({ viewport:{width:1024,height:1536}, deviceScaleFactor:1, colorScheme:'light' });
await p.addInitScript(()=>{try{localStorage.setItem('usr.theme','light')}catch{}});
await p.goto('http://localhost:5311/market?newui=1',{waitUntil:'domcontentloaded'});
await p.waitForTimeout(3000);
console.log(await p.evaluate(()=>{
  const pg=document.querySelector('.nmo .page'); const cs=getComputedStyle(pg); const r=pg.getBoundingClientRect();
  const main=pg.closest('.app-main')||pg.parentElement; const mr=main.getBoundingClientRect();
  return `page x=${Math.round(r.x)} w=${Math.round(r.width)} pad=${cs.padding} box=${cs.boxSizing} maxw=${cs.maxWidth}\n`
   +`parent .${main.className} x=${Math.round(mr.x)} w=${Math.round(mr.width)} pad=${getComputedStyle(main).padding}`;
}));
await b.close();
