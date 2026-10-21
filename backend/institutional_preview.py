"""DEV-ONLY standalone preview for the Institutional Intelligence dashboard.
Serves the read-only endpoints from institutional_dashboard_service (TRDGO-US-DEV
only) plus a single-page dashboard. No production connection, no auth coupling,
no Earnings-AI import. Run:  uvicorn institutional_preview:app --port 8099
"""
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse
import institutional_dashboard_service as ins

app = FastAPI(title="TRDGO Institutional Intelligence (dev preview)")


@app.get("/api/institutional/overview")
def _ov(): return ins.overview()
@app.get("/api/institutional/stock")
def _st(ticker: str, as_of: str = ""): return ins.stock(ticker, as_of or None)
@app.get("/api/institutional/rankings")
def _rk(kind: str = "accumulation", limit: int = 20, offset: int = 0): return ins.rankings(kind, limit, offset)
@app.get("/api/institutional/managers")
def _mg(q: str = "", quarter: str = "2026-Q2", limit: int = 25, offset: int = 0): return ins.managers(q, quarter, limit, offset)
@app.get("/api/institutional/history")
def _hi(ticker: str): return ins.history(ticker)
@app.get("/api/institutional/filing-metadata")
def _fm(): return ins.filing_metadata()
@app.get("/api/institutional/mapping")
def _mp(confidence: str = "", limit: int = 100, offset: int = 0): return ins.mapping(confidence, limit, offset)
@app.get("/api/institutional/health")
def _h(): return JSONResponse({"available": ins.available(), "source": ins.SOURCE})


# Aliases under the /api/institutional-intel/* prefix the integrated React
# frontend calls (the main backend registers the same paths; here they let the
# real app shell render against the dev-only dataset with no production touch).
@app.get("/api/institutional-intel/overview")
def _ov2(): return ins.overview()
@app.get("/api/institutional-intel/stock")
def _st2(ticker: str, as_of: str = ""): return ins.stock(ticker, as_of or None)
@app.get("/api/institutional-intel/rankings")
def _rk2(kind: str = "accumulation", limit: int = 20, offset: int = 0): return ins.rankings(kind, limit, offset)
@app.get("/api/institutional-intel/managers")
def _mg2(q: str = "", quarter: str = "2026-Q2", limit: int = 25, offset: int = 0): return ins.managers(q, quarter, limit, offset)
@app.get("/api/institutional-intel/history")
def _hi2(ticker: str): return ins.history(ticker)
@app.get("/api/institutional-intel/filing-metadata")
def _fm2(): return ins.filing_metadata()
@app.get("/api/institutional-intel/mapping")
def _mp2(confidence: str = "", limit: int = 100, offset: int = 0): return ins.mapping(confidence, limit, offset)
@app.get("/api/institutional-intel/health")
def _h2(): return JSONResponse({"available": ins.available(), "source": ins.SOURCE})


DASH = """<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>Institutional Intelligence</title>
<style>
:root{--bg:#0b0e14;--panel:#141922;--panel2:#1b212c;--bd:#232c3a;--tx:#e6edf3;--mut:#8b98a9;--acc:#2f81f7;
--buy:#2ea043;--sell:#f85149;--neu:#d29922;--good:#2ea043;--mid:#d29922}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font:14px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
.top{display:flex;align-items:center;gap:12px;padding:14px 20px;border-bottom:1px solid var(--bd);background:var(--panel)}
.top h1{font-size:17px;margin:0;font-weight:700}.badge{font-size:11px;padding:2px 8px;border-radius:999px;background:#1f6feb22;color:#58a6ff;border:1px solid #1f6feb55}
.warn{margin:14px 20px;padding:10px 14px;border-radius:8px;background:#d2992215;border:1px solid #d2992255;color:#e3b341;font-size:12.5px}
.tabs{display:flex;gap:4px;padding:0 20px;border-bottom:1px solid var(--bd);background:var(--panel);flex-wrap:wrap}
.tab{padding:10px 14px;cursor:pointer;color:var(--mut);border-bottom:2px solid transparent;font-weight:600;font-size:13px}
.tab.on{color:var(--tx);border-bottom-color:var(--acc)}
.wrap{padding:18px 20px;max-width:1200px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:18px}
.card{background:var(--panel);border:1px solid var(--bd);border-radius:10px;padding:14px}
.card .k{font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:var(--mut)}.card .v{font-size:22px;font-weight:700;margin-top:4px}
table{width:100%;border-collapse:collapse;font-size:13px}th,td{padding:8px 10px;text-align:right;border-bottom:1px solid var(--bd);white-space:nowrap}
th{font-size:11px;text-transform:uppercase;color:var(--mut);cursor:pointer}th.l,td.l{text-align:left}
.pos{color:var(--buy)}.neg{color:var(--sell)}
.pill{padding:2px 8px;border-radius:999px;font-weight:700;font-size:11px}
.pill.bullish{background:#2ea04322;color:#3fb950}.pill.bearish{background:#f8514922;color:#ff7b72}.pill.neutral{background:#d2992222;color:#e3b341}
.conf{font-size:11px;padding:1px 7px;border-radius:999px}.conf.HIGH{background:#2ea04322;color:#3fb950}.conf.MEDIUM{background:#d2992222;color:#e3b341}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:14px}
input,select{background:var(--panel2);border:1px solid var(--bd);color:var(--tx);border-radius:8px;padding:8px 10px;font-size:13px}
.mut{color:var(--mut);font-size:12px}.sec{font-size:15px;font-weight:700;margin:18px 0 8px}
.bar{height:10px;background:var(--panel2);border-radius:6px;overflow:hidden;display:inline-block;width:120px;vertical-align:middle}
.bar>i{display:block;height:100%}
.note{font-size:11px;color:var(--mut);margin-top:6px}
@media(max-width:640px){.cards{grid-template-columns:repeat(2,1fr)}th,td{padding:6px}.wrap{padding:12px}}
</style></head><body>
<div class=top><h1>🏛 Institutional Intelligence</h1><span class=badge>SEC 13F · DEV</span>
<span class=mut id=fresh></span></div>
<div class=warn>⚠ SEC Form 13F is <b>delayed quarterly disclosure</b> (filed up to 45 days after quarter-end) — <b>not live order flow</b>.
Institutional <b>BULLISH/BEARISH/NEUTRAL</b> labels describe ownership change only and are <b>separate from the TRDGO BUY/SELL trading signals</b>.</div>
<div class=tabs id=tabs></div>
<div class=wrap id=view></div>
<script>
const $=s=>document.querySelector(s), fmt=n=>n==null?'—':Number(n).toLocaleString();
const sign=n=>n==null?'—':(n>0?'+':'')+Number(n).toLocaleString();
const pct=n=>n==null?'—':(n>0?'+':'')+n+'%';
const api=async(p)=>(await fetch('/api/institutional/'+p)).json();
const TABS=[['overview','Overview'],['stock','Stock Analyzer'],['rankings','Activity Rankings'],['managers','Manager Explorer'],['history','History'],['mapping','CUSIP Mapping']];
let cur='overview';
function tabs(){$('#tabs').innerHTML=TABS.map(([k,l])=>`<div class="tab ${k==cur?'on':''}" onclick="go('${k}')">${l}</div>`).join('')}
window.go=k=>{cur=k;tabs();render()};
async function render(){const v=$('#view');v.innerHTML='<div class=mut>Loading…</div>';
 try{
 if(cur=='overview'){const o=await api('overview');$('#fresh').textContent='latest '+o.latest_quarter+' · '+o.meta.filing_date_range.join(' … ');
   v.innerHTML=`<div class=cards>
   ${card('Managers',fmt(o.total_managers))}${card('Reported holdings',fmt(o.total_reported_holdings))}${card('Securities',fmt(o.securities_tracked))}
   ${card('Latest quarter',o.latest_quarter)}${card('New positions',fmt(o.new_positions))}${card('Increased',fmt(o.increased_positions))}
   ${card('Reduced',fmt(o.reduced_positions))}${card('Closed',fmt(o.closed_positions))}${card('Unchanged',fmt(o.unchanged_positions))}
   ${card('Net share change',sign(o.total_reported_share_change))}</div>
   <div class=note>Comparison ${o.comparison}. Change counts are per manager+CUSIP. ${o.meta.disclosure}</div>`;}
 else if(cur=='stock'){v.innerHTML=`<div class=row><input id=tk placeholder="Ticker (e.g. AAPL)" value="AAPL" onkeydown="if(event.key=='Enter')loadStock()">
   <input id=asof placeholder="as-of date (optional, YYYY-MM-DD)"><button onclick=loadStock()>Analyze</button></div><div id=sres></div>`;loadStock();}
 else if(cur=='rankings'){const sel=`<select id=rkind onchange=loadRank()>
   <option value=accumulation>Top accumulation</option><option value=distribution>Top distribution</option>
   <option value=largest_increase>Largest share increases</option><option value=largest_decrease>Largest share decreases</option>
   <option value=most_new>Most new positions</option><option value=most_closed>Most closed positions</option>
   <option value=strongest_change>Strongest change</option></select>`;
   v.innerHTML=`<div class=row>${sel}<span class=mut>Fund-count direction is shown separately from net-share direction.</span></div><div id=rres></div>`;loadRank();}
 else if(cur=='managers'){v.innerHTML=`<div class=row><input id=mq placeholder="Filter manager name" onkeydown="if(event.key=='Enter')loadMgr()"><button onclick=loadMgr()>Search</button></div><div id=mres></div>`;loadMgr();}
 else if(cur=='history'){v.innerHTML=`<div class=row><input id=htk placeholder="Ticker" value="NVDA" onkeydown="if(event.key=='Enter')loadHist()"><button onclick=loadHist()>Show</button></div><div id=hres></div>`;loadHist();}
 else if(cur=='mapping'){const m=await api('mapping?limit=200');
   v.innerHTML=`<div class=row><span class=mut>Confidence:</span> ${Object.entries(m.confidence_counts).map(([k,n])=>`<span class="conf ${k}">${k} ${n}</span>`).join(' ')}</div>
   <table><thead><tr><th class=l>Ticker</th><th class=l>Issuer (SEC)</th><th class=l>CUSIP</th><th class=l>Type</th><th class=l>Exch</th><th class=l>Confidence</th><th class=l>Status</th></tr></thead><tbody>
   ${m.rows.map(r=>`<tr><td class=l><b>${r.ticker||'—'}</b></td><td class=l>${r.sec_issuer_name}</td><td class=l>${r.cusip}</td><td class=l>${r.security_type||'—'}</td><td class=l>${r.exchange||'—'}</td><td class=l><span class="conf ${r.confidence}">${r.confidence}</span></td><td class=l class=mut>${r.status}</td></tr>`).join('')}
   </tbody></table><div class=note>SEC issuer names &amp; CUSIPs preserved. HIGH=exact CUSIP+name match; MEDIUM=CUSIP match, name variant (not auto-promoted).</div>`;}
 }catch(e){v.innerHTML='<div class=warn>Error: '+e+'</div>'}
}
const card=(k,v)=>`<div class=card><div class=k>${k}</div><div class=v>${v}</div></div>`;
window.loadStock=async()=>{const t=$('#tk').value, a=$('#asof').value; const s=await api('stock?ticker='+encodeURIComponent(t)+(a?'&as_of='+a:''));
 const d=$('#sres'); if(s.status!='OK'){d.innerHTML='<div class=warn>'+(s.status||'not found')+'</div>';return}
 d.innerHTML=`<div class=sec>${s.company_name} <span class="conf ${s.mapping_confidence}">${s.mapping_confidence}</span> <span class=mut>${s.ticker} · ${s.cusip} · ${s.security_type||''} ${s.exchange||''}</span></div>
 <div class=cards>${card('Holders',fmt(s.institutional_holders))}${card('Total shares',fmt(s.total_reported_shares))}
 ${card('Prev quarter',fmt(s.previous_quarter_shares))}${card('Net change',sign(s.net_share_change))}${card('% change',pct(s.pct_change))}</div>
 <div class=cards>${card('New',fmt(s.new_positions))}${card('Increased',fmt(s.increased_positions))}${card('Reduced',fmt(s.reduced_positions))}${card('Closed',fmt(s.closed_positions))}${card('Unchanged',fmt(s.unchanged_positions))}</div>
 <div class=row><span>Institutional classification: <span class="pill ${s.institutional_classification}">${s.institutional_classification.toUpperCase()}</span></span>
 <span class=mut>${s.classification_basis}</span></div>
 <div class=note>Net-share direction: <b>${s.net_share_direction}</b> (can differ from the fund-count classification). Latest filing ${s.latest_filing_date}. ${a?('As-of '+a+' (point-in-time).'):''}</div>`;
};
window.loadRank=async()=>{const k=$('#rkind').value; const r=await api('rankings?kind='+k+'&limit=20');
 $('#rres').innerHTML=`<table><thead><tr><th class=l>Ticker</th><th class=l>Company</th><th>Q1 sh</th><th>Q2 sh</th><th>Net</th><th>%</th><th>New</th><th>Inc</th><th>Red</th><th>Closed</th><th>Holders</th></tr></thead><tbody>
 ${r.rows.map(x=>`<tr><td class=l><b>${x.ticker||'—'}</b></td><td class=l>${x.company_name}</td><td>${fmt(x.q1_shares)}</td><td>${fmt(x.q2_shares)}</td><td class="${x.net_share_change>0?'pos':'neg'}">${sign(x.net_share_change)}</td><td class="${x.pct_change>0?'pos':'neg'}">${pct(x.pct_change)}</td><td>${x.new_positions}</td><td>${x.increased}</td><td>${x.reduced}</td><td>${x.closed}</td><td>${x.holders}</td></tr>`).join('')}
 </tbody></table>`;
};
window.loadMgr=async()=>{const q=$('#mq')?.value||''; const m=await api('managers?limit=30&q='+encodeURIComponent(q));
 $('#mres').innerHTML=`<div class=mut>${m.total} managers filed ${m.quarter}</div><table><thead><tr><th class=l>Manager</th><th>Holdings</th><th>Securities</th><th>Latest filing</th></tr></thead><tbody>
 ${m.rows.map(x=>`<tr><td class=l>${x.manager_name}</td><td>${fmt(x.holdings)}</td><td>${x.securities_held}</td><td>${x.latest_filing_date}</td></tr>`).join('')}</tbody></table>`;
};
window.loadHist=async()=>{const t=$('#htk').value; const h=await api('history?ticker='+encodeURIComponent(t));
 if(h.status!='OK'){$('#hres').innerHTML='<div class=warn>'+h.status+'</div>';return}
 const mx=Math.max(...h.series.map(s=>s.reported_shares));
 $('#hres').innerHTML=`<div class=sec>${t} reported institutional shares by quarter</div>
 ${h.series.map(s=>`<div class=row><span style=width:80px>${s.quarter}</span><span class=bar><i style="width:${(s.reported_shares/mx*100).toFixed(0)}%;background:var(--acc)"></i></span>
  <span>${fmt(s.reported_shares)} sh · ${fmt(s.holders)} holders · filed ${s.latest_filing_date}</span></div>`).join('')}
 <div class=note>Quarterly reported holdings only (no fabricated quarters).</div>`;
};
tabs();render();
</script></body></html>"""

@app.get("/", response_class=HTMLResponse)
def _dash(): return DASH
