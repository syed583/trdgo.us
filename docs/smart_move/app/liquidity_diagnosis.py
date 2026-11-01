import sys,json,statistics,collections
from pathlib import Path
wt=Path(r"C:\Users\initi\AppData\Local\Temp\claude\C--Users-initi-Downloads-US-STOCKS\f48be4ee-7f76-4cb9-a237-d4759a147702\scratchpad\wt_smart")
sys.path.insert(0,str(wt/"docs/smart_move/shadow_impl"))
from smvshadow import adapters as ad
run=Path(r"C:\Users\initi\SmartMoveRun\out\run_20261101_full_universe")
rep=json.load(open(run/"audit_report.json"))
ev={}
for l in open(run/"store/evidence.jsonl"):
    e=json.loads(l)
    if e["kind"]=="ohlc": ev[e["symbol"]]=e   # latest wins
out=collections.Counter(); det=[]
for q in rep["results"]:
    codes=q["continuation"]["reason_codes"]
    if "ILLIQUID_OR_UNKNOWN" not in codes: continue
    e=ev.get(q["symbol"])
    if not e: out["NO_OHLC_EVIDENCE"]+=1; det.append((q["symbol"],"no ohlc"));continue
    pj=None
    h=e["raw_sha256"]; f=run/"store/payloads"/h[:2]/h[2:4]/(h+".json.gz"); pj=f if f.exists() else None
    if not pj: out["NO_PAYLOAD"]+=1; continue
    import gzip; d=json.loads(gzip.open(pj,'rt').read()); rows=d.get("data",d) if isinstance(d,dict) else d
    ser=ad.regular_series(rows if isinstance(rows,list) else rows.get("data",[]))
    ds=sorted(ser)
    if len(ds)<20: out["FEWER_THAN_20_BARS"]+=1; det.append((q["symbol"],len(ds),"bars"));continue
    miss=[x for x in ds[-20:] if not ser[x][4]]
    adv=statistics.mean(ser[x][3]*ser[x][4] for x in ds[-20:])
    if miss: out["ZERO_VOLUME_BARS"]+=1; det.append((q["symbol"],"zerovol",len(miss)))
    elif adv<20e6: out["GENUINE_LOW_ADV"]+=1; det.append((q["symbol"],round(adv/1e6,1)))
    else: out["ADV_OK_BUT_FLAGGED"]+=1; det.append((q["symbol"],round(adv/1e6,1),"OK?"))
print(out); print(det)
