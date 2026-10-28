"""READ-ONLY earnings model verification for the production VPS (written, not yet run there).

What it does, per symbol and per model (Claude, Claude Pro, Gemini, Grok, ChatGPT, Meta, Kimi,
Pre-Earnings Direction):
  stage cache : reads the cached analysis (earn:equity / earn:trade) -- never builds it -- re-runs the
                model's own score_one() on those cached inputs and prints every parameter's base weight,
                bias, points and rescaled max, the final score/signal/coverage, and the age of each
                cache entry. No provider call is made.
  stage raw   : (optional, ON REQUEST) GET-only provider reads for the same symbols at BACKGROUND priority,
                printing each dataset's own latest timestamp, row count and the derived parameter bias, so
                the cached bias can be compared with what the provider returns now.
  stage db    : SELECT-only counts from the prediction tables inside a READ ONLY transaction.

It never writes: no swr.serve (which can build), no scan(), capture(), grade(), backfill_*, no INSERT/UPDATE/
DELETE, no model/weight/threshold edit. Output goes to a JSON file plus a printed summary.

Usage (backend dir, production venv, APP_ENV=production so the DB guard allows the read):
    python tools\\verify_earnings_readonly.py --symbols AAPL,MSFT --stage cache --out C:\\audit-reports\\earn_cache.json
    python tools\\verify_earnings_readonly.py --symbols AAPL,MSFT --stage raw   --out C:\\audit-reports\\earn_raw.json
    python tools\\verify_earnings_readonly.py --stage db --out C:\\audit-reports\\earn_db.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

MODELS = ["claude", "claude_pro", "gemini", "grok", "chatgpt", "meta", "kimi", "ped"]


def _iso(ts):
    return None if ts is None else datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


def _age_h(ts):
    return None if ts is None else round((time.time() - ts) / 3600.0, 2)


def cache_stage(symbols, peek=None, held_at=None):
    """Re-score from CACHED inputs only. `peek`/`held_at` are injectable for the offline self-test."""
    import swr
    peek = peek or swr.peek
    held_at = held_at or swr.held_at
    import chatgpt_service as cg
    import claude_last_service as cl
    import claude_pro_service as cp
    import gemini_service as gm
    import grok_service as gk
    import kimi_service as ki
    import meta_service as mt
    import pre_earnings_direction_service as ped

    # Keep score_one() from reaching a provider: market-wide inputs come from the cache only.
    spy = peek("v3:spy15d")
    cl._spy_15d_return = lambda: spy if isinstance(spy, (int, float)) else None
    mkt = peek("kimi:market")
    mkt = mkt if isinstance(mkt, (int, float)) else None

    out = {"generated_at": _iso(time.time()), "spy15d_cached": spy, "kimi_market_cached": mkt, "symbols": {}}
    for sym in symbols:
        sym = sym.upper()
        eq = peek(f"earn:equity:{sym}")
        tr = peek(f"earn:trade:default:{sym}")
        rec = {"equity_cached_at": _iso(held_at(f"earn:equity:{sym}")), "equity_age_h": _age_h(held_at(f"earn:equity:{sym}")),
               "trade_cached_at": _iso(held_at(f"earn:trade:default:{sym}")), "trade_age_h": _age_h(held_at(f"earn:trade:default:{sym}")),
               "equity_params": None, "models": {}}
        if not eq or not eq.get("params"):
            rec["status"] = "NO_CACHED_EQUITY"
            out["symbols"][sym] = rec
            continue
        rec["status"] = "OK"
        rec["equity_params"] = [{"name": p.get("name"), "bias": p.get("bias"), "detail": p.get("detail"),
                                 "unavailable_reason": p.get("unavailable_reason")} for p in eq.get("params", [])]
        rec["raw_signals"] = eq.get("signals")
        rec["short_interest"] = eq.get("short_interest")
        rec["historical_moves_n"] = len((tr or {}).get("historical_moves") or [])
        rec["expected_move_pct"] = ((tr or {}).get("expected_move") or {}).get("percent")
        row = {"date": ((tr or {}).get("report_date") or None), "report_time": (tr or {}).get("report_time")}
        calls = {
            "claude": lambda: cl.score_one(sym, eq, tr, row),
            "claude_pro": lambda: cp.score_one(sym, eq, tr, row, with_analyst=False, mkt=mkt),
            "gemini": lambda: gm.score_one(sym, eq, tr, row, hit_rate=peek(f"gemini:hitrate:{sym}")
                                           if isinstance(peek(f"gemini:hitrate:{sym}"), (int, float)) else None),
            "grok": lambda: gk.score_one(sym, eq, tr, row, with_analyst=False),
            "chatgpt": lambda: cg.score_one(sym, eq, tr, row, with_analyst=False),
            "meta": lambda: mt.score_one(sym, eq, tr, row),
            "kimi": lambda: ki.score_one(sym, eq, tr, row, with_analyst=False, mkt=mkt),
            "ped": lambda: ped.score_one(sym, eq, tr, row),
        }
        for m in MODELS:
            try:
                r = calls[m]()
                rec["models"][m] = {
                    "score": r.get("score"), "signal": r.get("signal"), "tier": r.get("tier"),
                    "raw_signal": r.get("raw_signal"), "coverage": r.get("coverage"), "im_gate": r.get("im_gate"),
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
                v = fn()
                rec[name] = {"ok": True, "seconds": round(time.time() - t0, 2), **v}
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

        grab("estimates", lambda: {**rows_info(uw._rows(uw.earnings_estimates(sym)), "date"),
                                   "eps_bias": eqs._estimate_bias(sym)[0], "rev_bias": eqs._estimate_bias(sym)[1],
                                   "guidance_bias": eqs._guidance_bias(sym)})
        grab("earnings_history", lambda: {**rows_info(uw._rows(uw.earnings_history(sym)), "report_date"),
                                          "history_bias": eqs._history_bias(sym)})
        grab("daily_candles", lambda: rows_info(uw._rows(uw.candles(sym, "1d", limit=300)), "date"))
        grab("historical_moves", lambda: {"n": len(ets._historical_moves(sym)),
                                          "last": (ets._historical_moves(sym) or [None])[-1]})
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
    db = SessionLocal()
    out = {"generated_at": _iso(time.time()), "tables": {}}
    try:
        db.execute(text("SET TRANSACTION READ ONLY"))        # the server itself refuses any write below
        qs = {
            "model_predictions_by_model": "SELECT model, COUNT(*) predictions, COUNT(*) FILTER (WHERE evaluated_at IS NOT NULL) evaluated, COUNT(*) FILTER (WHERE evaluated_at IS NULL) pending FROM model_predictions GROUP BY model ORDER BY model",
            "model_predictions_by_signal": "SELECT model, signal, COUNT(*) n, COUNT(*) FILTER (WHERE evaluated_at IS NOT NULL) evaluated, COALESCE(SUM(correct),0) correct, ROUND(AVG(actual_move_pct)::numeric,2) avg_actual_move_pct FROM model_predictions GROUP BY model, signal ORDER BY model, signal",
            "model_predictions_range": "SELECT MIN(snapshot_date) first_snapshot, MAX(snapshot_date) last_snapshot, MIN(predicted_at) first_predicted_at, MAX(predicted_at) last_predicted_at, COUNT(*) total, COUNT(*) FILTER (WHERE predicted_at::date <> snapshot_date) predicted_on_other_day FROM model_predictions",
        }
        for k, q in qs.items():
            try:
                out["tables"][k] = [dict(r._mapping) for r in db.execute(text(q))]
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                db.execute(text("SET TRANSACTION READ ONLY"))
                out["tables"][k] = {"error": f"{type(exc).__name__}: {str(exc)[:160]}"}
    finally:
        db.rollback()
        db.close()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="")
    ap.add_argument("--stage", choices=["cache", "raw", "db"], required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    syms = [s for s in a.symbols.replace(" ", "").split(",") if s]
    if a.stage in ("cache", "raw") and not syms:
        sys.exit("--symbols is required for this stage")
    if a.stage == "raw" and len(syms) > 12:
        sys.exit("raw stage is limited to 12 symbols (provider budget)")
    res = {"cache": lambda: cache_stage(syms), "raw": lambda: raw_stage(syms), "db": db_stage}[a.stage]()
    Path(a.out).write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    print(f"wrote {a.out}")
    if a.stage == "cache":
        for s, rec in res["symbols"].items():
            print(s, rec.get("status"), "equity_age_h", rec.get("equity_age_h"), "trade_age_h", rec.get("trade_age_h"))
            for m, r in rec.get("models", {}).items():
                print(f"   {m:11s}", r.get("error") or f"score {r['score']} signal {r['signal']} tier {r['tier']} cov {r['coverage']}")


if __name__ == "__main__":
    main()
