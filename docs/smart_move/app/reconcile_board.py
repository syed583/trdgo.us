import json,urllib.request,collections
rep=json.load(open(r"C:\Users\initi\SmartMoveRun\out\run_20261101_full_universe\audit_report.json"))
R={r["symbol"]:r for r in rep["results"]}
bad=collections.Counter(); tot=collections.Counter()
for var in ("continuation","contrarian"):
    b=json.load(urllib.request.urlopen("http://127.0.0.1:5194/api/smart-move/board?variant="+var))
    assert b["total"]==122==len(R) and {r["symbol"] for r in b["rows"]}==set(R)
    for r in b["rows"]:
        e=R[r["symbol"]][var]; codes=set(e["reason_codes"]); st=r["status"]; tot[(var,st)]+=1
        sig=e["signal"]
        if st in("BUY","SELL"):
            ok = sig==st and "RULE" in codes and R[r["symbol"]]["available_points"]>=24
        elif st=="NO_TRADE":
            ok = sig=="NO_TRADE" and not (codes & {"NOT_TRADABLE","INSUFFICIENT_COVERAGE","UNKNOWN_TIMING","STALE_EVIDENCE"}) and (("ILLIQUID_OR_UNKNOWN" not in codes) or ("under $20 million" in r["status_why"] and not any(m.startswith("F5") for m in R[r["symbol"]]["missing_factors"])))
        elif st=="DATA_INCOMPLETE": ok = sig=="NO_TRADE" and (codes & {"NOT_TRADABLE","ILLIQUID_OR_UNKNOWN","INSUFFICIENT_COVERAGE","UNKNOWN_TIMING","STALE_EVIDENCE"} or bool(R[r["symbol"]]["missing_factors"]))
        else: ok=False
        # no BUY/SELL engine signal may be hidden as grey/yellow without a gate code
        if sig in("BUY","SELL") and st not in("BUY","SELL"): ok = bool(codes & {"NOT_TRADABLE","ILLIQUID_OR_UNKNOWN","INSUFFICIENT_COVERAGE","BLOCKED_BY_GATE","STALE_EVIDENCE"})
        if not ok: bad[(var,r["symbol"],st,sig)]+=1
print(dict(tot)); print("mismatches:",dict(bad))
