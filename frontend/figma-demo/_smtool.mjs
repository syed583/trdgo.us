import { chromium } from 'playwright';
const b = await chromium.launch();
const p = await b.newPage({ viewport:{width:1536,height:1024}, deviceScaleFactor:1, colorScheme:'dark' });
await p.addInitScript(()=>{try{localStorage.setItem('usr.theme','dark')}catch{}});
await p.goto('http://localhost:5311/smart-move/engine20?newui=1',{waitUntil:'domcontentloaded'});
await p.waitForTimeout(3000);
const before = await p.$$eval('[data-testid="sm-live-row"]', n=>n.length);
await p.fill('.sm-find input', 'NVDA');
await p.waitForTimeout(400);
const after = await p.$$eval('[data-testid="sm-live-row"]', n=>n.length);
// the export must write the rows on screen, so drive it and read the file back
const dl = p.waitForEvent('download', {timeout: 5000}).catch(()=>null);
await p.click('[data-testid="sm-live-export"]');
const d = await dl;
let name='(no download)', head='';
if (d) { name = d.suggestedFilename(); const s = await d.createReadStream(); head = (await new Promise(r=>{let x='';s.on('data',c=>x+=c);s.on('end',()=>r(x))})).split('\r\n').slice(0,3).join('\n'); }
console.log(`rows before search=${before}  after "NVDA"=${after}\ndownload: ${name}\n${head}`);
await b.close();
