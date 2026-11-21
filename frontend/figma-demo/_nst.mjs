import { chromium } from 'playwright';
const b = await chromium.launch();
const p = await b.newPage({ viewport:{width:1024,height:1536}, deviceScaleFactor:1, colorScheme:'dark' });
await p.addInitScript(()=>{try{localStorage.setItem('usr.theme','dark')}catch{}});
await p.goto('http://localhost:5311/strategy-trades?newui=1',{waitUntil:'domcontentloaded'});
await p.waitForTimeout(3200);
console.log(await p.evaluate(()=>{
  const card=document.querySelector('.nst-card');
  const out=[`card h=${Math.round(card.getBoundingClientRect().height)}`];
  [...card.children].forEach(c=>out.push(`  <${c.tagName.toLowerCase()}.${c.className}> h=${Math.round(c.getBoundingClientRect().height)}`));
  const tr=card.querySelectorAll('tbody tr');
  out.push(`rows=${tr.length} first h=${tr[0]?Math.round(tr[0].getBoundingClientRect().height):'-'} thead h=${Math.round(card.querySelector('thead').getBoundingClientRect().height)}`);
  return out.join('\n');
}));
await b.close();
