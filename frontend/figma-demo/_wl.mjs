import { chromium } from 'playwright';
const b = await chromium.launch();
for (const theme of ['dark','light']) {
  const p = await b.newPage({ viewport:{width:1440,height:1000}, deviceScaleFactor:1, colorScheme:theme });
  await p.addInitScript((t)=>{ try{ localStorage.setItem('usr.theme',t); localStorage.removeItem('trdgo.watchlist.v1'); }catch{} }, theme);
  const errs=[]; p.on('console', m=>{ if(m.type()==='error') errs.push(m.text().slice(0,90)); });
  await p.goto('http://localhost:5311/watchlist?newui=1',{waitUntil:'domcontentloaded'});
  await p.evaluate(()=>document.fonts.ready);
  await p.waitForTimeout(6000);                       // the TradingView embeds load from their CDN
  if (theme==='dark') { await p.fill('.nwl-find input','AB'); await p.waitForTimeout(900); }
  await p.screenshot({ path:`_compare/live/_watchlist_${theme}.png` });
  const tv = await p.$$eval('.tradingview-widget-container iframe', n=>n.length);
  const attr = await p.$$eval('.tradingview-widget-copyright', n=>n.length);
  const res = await p.$$eval('.nwl-results li', n=>n.length);
  console.log(`${theme}: tv iframes=${tv} attribution blocks=${attr} search results=${res} consoleErrors=${errs.length}${errs.length?' :: '+errs[0]:''}`);
  await p.close();
}
await b.close();
