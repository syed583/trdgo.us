import { chromium } from 'playwright';
const b = await chromium.launch();
for (const [who, admin] of [['non-admin', '0'], ['admin', '1']]) {
  const p = await b.newPage({ viewport:{width:1536,height:1024}, deviceScaleFactor:1, colorScheme:'dark' });
  await p.addInitScript(()=>{try{localStorage.setItem('usr.theme','dark')}catch{}});
  await p.goto(`http://localhost:5311/smart-move/engine20?newui=1&admin=${admin}`,{waitUntil:'domcontentloaded'});
  await p.waitForTimeout(3500);
  const r = await p.evaluate(()=>({
    sub: document.querySelector('.page-head p')?.innerText.slice(0,70),
    hasVersion: document.body.innerText.includes('score40-v1'),
  }));
  console.log(`${who.padEnd(10)} version visible=${r.hasVersion}  "${r.sub}"`);
  await p.close();
}
await b.close();
