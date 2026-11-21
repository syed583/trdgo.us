import { chromium } from 'playwright';
const b = await chromium.launch();
const p = await b.newPage({ viewport:{width:1536,height:1024}, deviceScaleFactor:1, colorScheme:'dark' });
await p.addInitScript(()=>{try{localStorage.setItem('usr.theme','dark')}catch{}});
await p.goto('http://localhost:5311/ai-insights?newui=1',{waitUntil:'domcontentloaded'});
await p.waitForTimeout(3000);
console.log(await p.evaluate(()=>{
  const out=[];
  document.querySelectorAll('.nal-rowD > *').forEach((card,i)=>{
    const h=card.querySelector('.nd-card-head');
    const pad=getComputedStyle(card);
    out.push(`card${i} h=${Math.round(card.getBoundingClientRect().height)} pad=${pad.paddingTop}/${pad.paddingBottom} head=${h?Math.round(h.getBoundingClientRect().height):'-'}`);
    const rows=card.querySelectorAll('.nal-sectors li, tbody tr');
    out.push(`  rows=${rows.length} pitch=${rows[1]&&rows[0]?Math.round(rows[1].getBoundingClientRect().top-rows[0].getBoundingClientRect().top):'-'} lastBottom=${rows.length?Math.round(rows[rows.length-1].getBoundingClientRect().bottom - card.getBoundingClientRect().top):'-'}`);
  });
  return out.join('\n');
}));
await b.close();
