import { chromium } from 'playwright';
const b = await chromium.launch();
const out = [];
for (const [label, qs] of [['new UI','?newui=1'], ['classic','?newui=0']]) {
  const p = await b.newPage({ viewport:{width:1024,height:1536}, deviceScaleFactor:1, colorScheme:'light' });
  await p.addInitScript(()=>{try{localStorage.setItem('usr.theme','light')}catch{}});
  const calls = [];
  p.on('request', r => { if (r.url().includes('/api/landing/sparklines')) calls.push(r.url()); });
  await p.goto(`http://localhost:5311/market${qs}`,{waitUntil:'domcontentloaded'});
  await p.waitForTimeout(3000);
  const n = await p.$$eval('.mo-index-spark', e=>e.length);
  out.push(`${label}: sparklines drawn=${n}  requests to /api/landing/sparklines=${calls.length}`);
  await p.close();
}
console.log(out.join('\n'));
await b.close();
