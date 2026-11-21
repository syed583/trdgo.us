import { chromium } from 'playwright';
const b = await chromium.launch();
const p = await b.newPage({ viewport:{width:1536,height:1024}, deviceScaleFactor:1, colorScheme:'dark' });
await p.addInitScript(()=>{try{localStorage.setItem('usr.theme','dark')}catch{}});
await p.goto('http://localhost:5311/ai-insights?newui=1',{waitUntil:'domcontentloaded'});
await p.waitForTimeout(3500);
const landing = await p.evaluate(()=>({
  statsRow: !!document.querySelector('.nal-stats'),
  vendors: ['Unusual Whales','Finnhub','Finviz'].filter(w=>document.body.innerText.includes(w)),
  strip: Math.round(document.querySelector('.nal-strip').getBoundingClientRect().bottom),
  rowC:  Math.round(document.querySelector('.nal-rowC').getBoundingClientRect().top),
  promoH: Math.round(document.querySelector('.nu-promo').getBoundingClientRect().height),
  navItems: document.querySelectorAll('.nu-link').length,
  navVisible: [...document.querySelectorAll('.nu-link')].filter(e=>{const r=e.getBoundingClientRect();return r.top>=0 && r.bottom<=1024;}).length,
}));
await p.goto('http://localhost:5311/news?newui=1',{waitUntil:'domcontentloaded'});
await p.waitForTimeout(3500);
const news = await p.evaluate(()=>({
  rendered: document.querySelectorAll('.nnw-list > li').length,
  pager: !!document.querySelector('.nnw-pager'),
  label: document.querySelector('.nnw-pager .nst-mute')?.innerText || null,
  buttons: [...document.querySelectorAll('.nnw-pager-btns button')].map(b=>b.innerText),
}));
console.log(JSON.stringify({landing, news}, null, 1));
await b.close();
