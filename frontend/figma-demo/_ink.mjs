import { chromium } from 'playwright';
const b = await chromium.launch();
const p = await b.newPage({ viewport:{width:1536,height:1024}, deviceScaleFactor:1, colorScheme:'dark' });
await p.addInitScript(()=>{try{localStorage.setItem('usr.theme','dark')}catch{}});
await p.goto('http://localhost:5311/ai-insights?newui=1',{waitUntil:'domcontentloaded'});
await p.waitForTimeout(3000);
console.log(await p.evaluate(()=>{
  const row=document.querySelector('.nal-rowC .anp-card .anp-trow.anp-click');
  if(!row) return 'no row';
  return [...row.children].map(c=>`${c.className||'(none)'} = ${getComputedStyle(c).color} | "${c.textContent.trim().slice(0,12)}"`).join('\n')
   + '\ncard bg: '+getComputedStyle(row.closest('.anp-card')).backgroundImage.slice(0,80);
}));
await b.close();
