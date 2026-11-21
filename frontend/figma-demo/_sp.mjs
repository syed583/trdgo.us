import { chromium } from 'playwright';
const b = await chromium.launch();
const p = await b.newPage({ viewport:{width:1536,height:1024}, deviceScaleFactor:1, colorScheme:'dark' });
await p.addInitScript(()=>{try{localStorage.setItem('usr.theme','dark')}catch{}});
await p.goto('http://localhost:5311/ai-insights?newui=1',{waitUntil:'domcontentloaded'});
await p.waitForTimeout(3000);
console.log(await p.evaluate(()=>{
  const sv=document.querySelectorAll('.anp-spark');
  const first=sv[0];
  const rows=[...document.querySelectorAll('.nal-rowC .anp-card .anp-trow.anp-click')].slice(0,3)
    .map(r=>r.children[0].textContent.trim()+' cells='+r.children.length);
  return `anp-spark count=${sv.length} `+(first?`box=${JSON.stringify(first.getBoundingClientRect().toJSON())} path=${first.querySelector('path')?.getAttribute('d')?.slice(0,30)}`:'none')+'\n'+rows.join('\n');
}));
await b.close();
