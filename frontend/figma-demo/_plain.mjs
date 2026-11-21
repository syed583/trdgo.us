import { chromium } from 'playwright';
const routes = [['calendar','/earnings-calendar'],['postearn','/post-earnings-forward'],['overview','/dashboard'],['optionsflow','/options-flow/NVDA']];
const b = await chromium.launch();
for (const [key, route] of routes) {
  for (const theme of ['dark','light']) {
    const p = await b.newPage({ viewport:{width:1440,height:900}, deviceScaleFactor:1, colorScheme:theme });
    await p.addInitScript((t)=>{try{localStorage.setItem('usr.theme',t)}catch{}}, theme);
    await p.goto(`http://localhost:5311${route}?newui=1`,{waitUntil:'domcontentloaded'});
    await p.evaluate(()=>document.fonts.ready);
    await p.waitForTimeout(2800);
    await p.screenshot({ path: `_compare/live/_plain_${key}_${theme}.png` });
    await p.close();
  }
  console.log('shot', key);
}
await b.close();
