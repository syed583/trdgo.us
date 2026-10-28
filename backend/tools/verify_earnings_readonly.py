"""READ-ONLY earnings model verification for the production VPS.

Stages
  pick   : lists the names the live tabs are scoring (from the cached Claude scan): symbol, earnings date,
           timing, score, signal, coverage. Reads one cache row. Nothing else.
  cache  : for the symbols you name, reads the cached analysis (earn:equity / earn:trade / hit-rate / SPY / VIX
           caches) and the cached tab scans, re-runs each model's own score_one() on those cached inputs with
           the network BLOCKED, and prints every parameter's base weight, bias, points and rescaled max, the
           final score/signal/coverage, the cache ages, and whether the result equals what the tab shows.
  raw    : (separate approval, after 20:00 ET) GET-only provider reads at background priority for up to 12
           symbols: each dataset's own latest timestamp, row count and the derived parameter bias.
  db     : SELECT-only counts from the prediction tables.

Safety: reads go through plain SELECTs inside SET TRANSACTION READ ONLY (the database itself refuses a write);
swr / db_cache are NOT imported (their first use runs CREATE TABLE IF NOT EXISTS); no scan(), capture(),
grade(), backfill_*; during scoring any socket connect raises; refuses to run unless APP_ENV=production so it
can never read a development namespace by mistake. Output is written only to the --out file.

Usage (run from C:\\apps\\us-stock-reader\\backend with the production venv; set DB_POOL_SIZE=1 DB_MAX_OVERFLOW=0):
    python <path>\\verify_earnings_readonly.py --stage pick  --out C:\\audit-reports\\earn_pick.json
    python <path>\\verify_earnings_readonly.py --stage cache --symbols AAPL,MSFT --out C:\\audit-reports\\earn_cache.json
    python <path>\\verify_earnings_readonly.py --stage db    --out C:\\audit-reports\\earn_db.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# The backend directory (cwd, or this file's parent when it lives in backend/tools).
for _p in (os.getcwd(), str(Path(__file__).resolve().parent.parent)):
    if os.path.exists(os.path.join(_p, "tab_settings.py")) and _p not in sys.path:
        sys.path.insert(0, _p)

MODELS = ["claude", "claude_pro", "gemini", "grok", "chatgpt", "meta", "kimi", "ped"]
SCAN_KEYS = {"claude": "v3:scan:v2:{d}", "claude_pro": "claudepro:scan:{d}", "gemini": "gemini:scan:{d}",
             "grok": "grok:scan:{d}", "chatgpt": "chatgpt:scan:{d}", "meta": "meta:scan:{d}",
             "kimi": "kimi:scan:{d}", "ped": "ped:scan:{d}"}


def _iso(ts):
    return None if ts is None else datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


def _age_h(ts):
    return None if ts is None else round((time.time() - ts) / 3600.0, 2)


def read_cache(keys):
    """SELECT-only read of swr_cache in a READ ONLY transaction. Production namespace = the bare key."""
    if (os.environ.get("APP_ENV") or "").strip().lower() != "production":
        sys.exit("APP_ENV is not 'production' in this process: refusing (would read the dev namespace).")
    from sqlalchemy import text
    from database import SessionLocal
    db = SessionLocal()
    try:
        db.execute(text("SET TRANSACTION READ ONLY"))
        rows = db.execute(text("SELECT cache_key, value, updated_at FROM swr_cache WHERE cache_key = ANY(:ks)"),
                          {"ks": list(keys)}).all()
        return {k: (float(ts), json.loads(v)) for k, v, ts in rows}
    finally:
        db.rollback()
        db.close()


class no_network:
    """While active, any socket connect raises: proves scoring cannot reach a provider."""

    def __enter__(self):
        import socket
        self._real = socket.socket.connect

        def blocked(*a, **k):
            raise RuntimeError("network blocked by the read-only verifier")
        socket.socket.connect = blocked
        return self

    def __exit__(self, *exc):
        import socket
        socket.socket.connect = self._real


def pick_stage(days=15, reader=None):
    reader = reader or read_cache
    key = SCAN_KEYS["claude"].format(d=days)
    got = reader([key]).get(key)
    if not got:
        return {"status": "NO_CACHED_SCAN", "key": key}
    ts, val = got
    cols = ("symbol", "company", "date", "days_to", "report_timing", "time_label", "score", "signal", "coverage", "market_cap")
    rows = [{k: r.get(k) for k in cols} for r in (val.get("rows") or [])]
    return {"status": "OK", "key": key, "scan_cached_at": _iso(ts), "scan_age_h": _age_h(ts), "n": len(rows), "rows": rows}


def cache_stage(symbols, days=15, reader=None):
    reader = reader or read_cache
    keys = ["v3:spy15d", "kimi:market"]
    for s in symbols:
        s = s.upper()
        keys += [f"earn:equity:{s}", f"earn:trade:default:{s}", f"gemini:hitrate:{s}"]
    keys += [k.format(d=days) for k in SCAN_KEYS.values()]
    cached = reader(keys)                      # the only database access in this stage

    def val(k):
        return cached[k][1] if k in cached else None

    def at(k):
        return cached[k][0] if k in cached else None

    import chatgpt_service as cg
    import claude_last_service as cl
    import claude_pro_service as cp
    import gemini_service as gm
    import grok_service as gk
    import kimi_service as ki
    import meta_service as mt
    import pre_earnings_direction_service as ped

    spy = val("v3:spy15d")
    spy = spy if isinstance(spy, (int, float)) else None
    cl._spy_15d_return = lambda: spy           # cached market input only; never a provider call
    mkt = val("kimi:market")
    mkt = mkt if isinstance(mkt, (int, float)) else None
    scans = {m: {(r.get("symbol") or "").upper(): r for r in ((val(k.format(d=days)) or {}).get("rows") or [])}
             for m, k in SCAN_KEYS.items()}

    out = {"generated_at": _iso(time.time()), "spy15d_cached": spy, "spy15d_cached_at": _iso(at("v3:spy15d")),
           "kimi_market_cached": mkt, "kimi_market_cached_at": _iso(at("kimi:market")),
           "scan_cached_at": {m: _iso(at(k.format(d=days))) for m, k in SCAN_KEYS.items()}, "symbols": {}}
    for sym in symbols:
        sym = sym.upper()
        ek, tk = f"earn:equity:{sym}", f"earn:trade:default:{sym}"
        eq, tr = val(ek), val(tk)
        rec = {"equity_cached_at": _iso(at(ek)), "equity_age_h": _age_h(at(ek)),
               "trade_cached_at": _iso(at(tk)), "trade_age_h": _age_h(at(tk)), "models": {}}
        if not eq or not eq.get("params"):
            rec["status"] = "NO_CACHED_EQUITY"
            out["symbols"][sym] = rec
            continue
        rec["status"] = "OK"
        rec["equity_params"] = [{"name": p.get("name"), "weight": p.get("weight"), "bias": p.get("bias"),
                                 "points": p.get("points"), "detail": p.get("detail"), "evidence": p.get("evidence"),
                                 "unavailable_reason": p.get("unavailable_reason")} for p in eq.get("params", [])]
        rec["raw_signals"] = eq.get("signals")
        rec["short_interest"] = eq.get("short_interest")
        hm = (tr or {}).get("historical_moves") or []
        rec["historical_moves_n"] = len(hm)
        rec["historical_moves_oldest_newest"] = [hm[0], hm[-1]] if hm else None
        rec["expected_move_pct"] = ((tr or {}).get("expected_move") or {}).get("percent")
        hr = val(f"gemini:hitrate:{sym}")
        hr = hr if isinstance(hr, (int, float)) else None
        base = scans["claude"].get(sym) or next((s[sym] for s in scans.values() if sym in s), {})
        row = {"company": base.get("company"), "date": base.get("date"), "short_label": base.get("time_label"),
               "time_label": base.get("report_timing"), "date_label": base.get("date_label")}
        calls = {
            "claude": lambda: cl.score_one(sym, eq, tr, row),
            "claude_pro": lambda: cp.score_one(sym, eq, tr, row, with_analyst=False, mkt=mkt),
            "gemini": lambda: gm.score_one(sym, eq, tr, row, hit_rate=hr),
            "grok": lambda: gk.score_one(sym, eq, tr, row, with_analyst=False),
            "chatgpt": lambda: cg.score_one(sym, eq, tr, row, with_analyst=False),
            "meta": lambda: mt.score_one(sym, eq, tr, row),
            "kimi": lambda: ki.score_one(sym, eq, tr, row, with_analyst=False, mkt=mkt),
            "ped": lambda: ped.score_one(sym, eq, tr, row),
        }
        for m in MODELS:
            try:
                with no_network():
                    r = calls[m]()
                s = scans[m].get(sym) or {}
                rec["models"][m] = {
                    "rescored": {"score": r.get("score"), "signal": r.get("signal"), "tier": r.get("tier"),
                                 "raw_signal": r.get("raw_signal"), "coverage": r.get("coverage"), "im_gate": r.get("im_gate")},
                    "tab_cached": {"score": s.get("score"), "signal": s.get("signal"), "tier": s.get("tier"),
                                   "coverage": s.get("coverage")},
                    "same_as_tab": ((s.get("score") == r.get("score") and s.get("signal") == r.get("signal"))
                                    if s else None),
                    "params": [{"label": p.get("label") or p.get("name"), "base_weight": p.get("base_max", p.get("max")),
                                "bias": p.get("bias"), "rescaled_max": p.get("max"), "points": p.get("points"),
                                "excluded": p.get("excluded")} for p in r.get("params", [])],
                }
            except Exception as exc:  # noqa: BLE001
                rec["models"][m] = {"error": f"{type(exc).__name__}: {exc}"}
        out["symbols"][sym] = rec
    return out


def raw_stage(symbols):
    """GET-only provider reads at background priority; reports each dataset's own latest timestamp."""
    import unusualwhales_service as uw
    uw.set_background(True)                    # yield to live trading; honour the 3-connection limit
    import earnings_equity_service as eqs
    import earnings_trade_service as ets
    out = {"generated_at": _iso(time.time()), "symbols": {}}
    for sym in symbols:
        sym = sym.upper()
        rec = {}

        def grab(name, fn):
            t0 = time.time()
            try:
                rec[name] = {"ok": True, "seconds": round(time.time() - t0, 2), **fn()}
            except Exception as exc:  # noqa: BLE001
                rec[name] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

        def rows_info(rows, *date_fields):
            rows = [r for r in rows if isinstance(r, dict)]
            latest = None
            for r in rows:
                for f in date_fields:
                    if r.get(f) and (latest is None or str(r[f]) > latest):
                        latest = str(r[f])
            return {"rows": len(rows), "latest": latest}

        def estimates():
            eps, rev = eqs._estimate_bias(sym)
            return {**rows_info(uw._rows(uw.earnings_estimates(sym)), "date"), "eps_bias": eps, "rev_bias": rev,
                    "guidance_bias": eqs._guidance_bias(sym)}

        def moves():
            m = ets._historical_moves(sym)
            return {"n": len(m), "oldest": (m or [None])[0], "newest": (m or [None])[-1]}

        grab("estimates", estimates)
        grab("earnings_history", lambda: {**rows_info(uw._rows(uw.earnings_history(sym)), "report_date"),
                                          "history_bias": eqs._history_bias(sym)})
        grab("daily_candles", lambda: rows_info(uw._rows(uw.candles(sym, "1d", limit=300)), "date"))
        grab("historical_moves", moves)
        grab("short_interest", lambda: {"meta": eqs._short_interest_meta(sym)})
        grab("dark_pool", lambda: {"bias": eqs._darkpool_bias(sym)})
        grab("sector_market_spy", lambda: {"bias": eqs._sector_bias(sym)})
        grab("peers", lambda: {"bias": eqs._peer_readthrough(sym)})
        grab("directional_signals", lambda: {"signals": eqs._directional_signals(sym)})
        out["symbols"][sym] = rec
    return out


def db_stage():
    from sqlalchemy import text
    from database import SessionLocal
    if (os.environ.get("APP_ENV") or "").strip().lower() != "production":
        sys.exit("APP_ENV is not 'production': refusing.")
    qs = {
        "by_model": "SELECT model, COUNT(*) predictions, COUNT(*) FILTER (WHERE evaluated_at IS NOT NULL) evaluated, "
                    "COUNT(*) FILTER (WHERE evaluated_at IS NULL) pending FROM model_predictions GROUP BY model ORDER BY model",
        "by_model_signal": "SELECT model, signal, COUNT(*) n, COUNT(*) FILTER (WHERE evaluated_at IS NOT NULL) evaluated, "
                           "COALESCE(SUM(correct),0) correct, ROUND(AVG(actual_move_pct)::numeric,2) avg_actual_move_pct "
                           "FROM model_predictions GROUP BY model, signal ORDER BY model, signal",
        "range": "SELECT MIN(snapshot_date) first_snapshot, MAX(snapshot_date) last_snapshot, MIN(predicted_at) first_predicted_at, "
                 "MAX(predicted_at) last_predicted_at, COUNT(*) total, "
                 "COUNT(*) FILTER (WHERE predicted_at::date <> snapshot_date) predicted_on_other_day, "
                 "COUNT(*) FILTER (WHERE snapshot_date > earnings_date) snapshot_after_earnings FROM model_predictions",
    }
    out = {"generated_at": _iso(time.time()), "tables": {}}
    for k, q in qs.items():
        db = SessionLocal()
        try:
            db.execute(text("SET TRANSACTION READ ONLY"))
            out["tables"][k] = [dict(r._mapping) for r in db.execute(text(q))]
        except Exception as exc:  # noqa: BLE001
            out["tables"][k] = {"error": f"{type(exc).__name__}: {str(exc)[:160]}"}
        finally:
            db.rollback()
            db.close()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="")
    ap.add_argument("--stage", choices=["pick", "cache", "raw", "db"], required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    syms = [s for s in a.symbols.replace(" ", "").split(",") if s]
    if a.stage in ("cache", "raw") and not syms:
        sys.exit("--symbols is required for this stage")
    if a.stage == "raw" and len(syms) > 12:
        sys.exit("raw stage is limited to 12 symbols (provider budget)")
    res = {"pick": pick_stage, "cache": lambda: cache_stage(syms), "raw": lambda: raw_stage(syms), "db": db_stage}[a.stage]()
    Path(a.out).write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    print(f"wrote {a.out}")
    if a.stage == "pick":
        print("scan cached", res.get("scan_cached_at"), "age_h", res.get("scan_age_h"), "names", res.get("n"))
        for r in sorted(res.get("rows", []), key=lambda r: (r.get("days_to") is None, r.get("days_to"))):
            print(f"{r['symbol']:6s} {str(r['date']):10s} d{r['days_to']} {str(r.get('report_timing') or r.get('time_label') or ''):10s} "
                  f"score {r['score']} {r['signal']} cov {r['coverage']}")
    if a.stage == "cache":
        for s, rec in res["symbols"].items():
            print(s, rec.get("status"), "equity_age_h", rec.get("equity_age_h"), "trade_age_h", rec.get("trade_age_h"))
            for m, r in rec.get("models", {}).items():
                if "error" in r:
                    print(f"   {m:11s} ERROR {r['error']}")
                else:
                    x, y = r["rescored"], r["tab_cached"]
                    print(f"   {m:11s} rescored {x['score']} {x['signal']} cov {x['coverage']} | tab {y['score']} {y['signal']} | same={r['same_as_tab']}")


if __name__ == "__main__":
    main()
