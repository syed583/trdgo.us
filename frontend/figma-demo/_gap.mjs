import { chromium } from 'playwright';
const b = await chromium.launch();
const p = await b.newPage({ viewport:{width:1536,height:1024}, deviceScaleFactor:1, colorScheme:'dark' });
await p.addInitScript(()=>{try{localStorage.setItem('usr.theme','dark')}catch{}});
await p.goto('http://localhost:5311/smart-move/engine20?newui=1',{waitUntil:'domcontentloaded'});
await p.waitForTimeout(3200);
console.log(await p.evaluate(()=>{
  const head=document.querySelector('.sm-fig-head');
  const out=[];
  let s=head.previousElementSibling;
  while(s){const r=s.getBoundingClientRect(); out.push(`prev <${s.tagName.toLowerCase()}.${s.className}> y=${Math.round(r.y)} h=${Math.round(r.height)}`); s=s.previousElementSibling;}
  const par=head.parentElement; const pr=par.getBoundingClientRect();
  out.push(`parent .${par.className} y=${Math.round(pr.y)} padTop=${getComputedStyle(par).paddingTop}`);
  const tabs=document.querySelector(".sm-tabs[role='tablist']");
  const tp=tabs.parentElement.getBoundingClientRect();
  out.push(`tabs wrap .${tabs.parentElement.className} y=${Math.round(tp.y)} h=${Math.round(tp.height)}`);
  return out.join('\n');
}));
await b.close();
