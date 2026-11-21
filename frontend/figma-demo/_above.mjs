import { chromium } from 'playwright';
const b = await chromium.launch();
const p = await b.newPage({ viewport:{width:1536,height:1024}, deviceScaleFactor:1, colorScheme:'dark' });
await p.addInitScript(()=>{try{localStorage.setItem('usr.theme','dark')}catch{}});
await p.goto('http://localhost:5311/smart-move/engine20?newui=1',{waitUntil:'domcontentloaded'});
await p.waitForTimeout(3200);
console.log(await p.evaluate(()=>{
  const tabs=document.querySelector(".sm-tabs[role='tablist']");
  const out=[];
  let n=tabs;
  // walk up to the main scroll container listing every preceding sibling that takes height
  while(n && n!==document.body){
    let s=n.previousElementSibling;
    while(s){ const r=s.getBoundingClientRect(); if(r.height>2) out.push(`${n.parentElement.className} > prev <${s.tagName.toLowerCase()}.${s.className}> y=${Math.round(r.y)} h=${Math.round(r.height)}`); s=s.previousElementSibling; }
    n=n.parentElement;
  }
  const main=document.querySelector('.app-main');
  out.push(`app-main padding=${getComputedStyle(main).padding}`);
  return out.join('\n');
}));
await b.close();
