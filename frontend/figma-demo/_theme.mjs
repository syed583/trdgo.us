import { chromium } from 'playwright';
const b = await chromium.launch();
const p = await b.newPage({ viewport:{width:1024,height:1536}, deviceScaleFactor:1, colorScheme:'light' });
await p.addInitScript(()=>{try{localStorage.setItem('usr.theme','light')}catch{}});
await p.goto('http://localhost:5311/news?newui=1',{waitUntil:'domcontentloaded'});
await p.waitForTimeout(3000);
console.log(await p.evaluate(()=>{
  const r=document.querySelector('.nu-rail');
  const cs=getComputedStyle(r);
  return `data-theme=${document.documentElement.getAttribute('data-theme')}\n`
    +`rail bg=${cs.backgroundImage.slice(0,70)}\n`
    +`--nu-rail-grad=${getComputedStyle(document.querySelector('.app-nu')).getPropertyValue('--nu-rail-grad').slice(0,70)}\n`
    +`link color=${getComputedStyle(document.querySelector('.nu-link')).color}`;
}));
await b.close();
