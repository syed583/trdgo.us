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
  fresh  : age buckets of the cached analyses for every calendar name, scan cache sizes/ages.
  snapshots: prediction capture-time audit (provisional BMO/AMC windows, ET), records for AMZN/EAT/GRMN,
           earnings-moves distribution, Claude Pro cache keys. SELECT-only, READ ONLY transactions.
  integrity: prediction-history integrity (one prediction per event by explicit definitions, accuracy with
           sample sizes and intervals, grading-by-timing, repeated scores, post-release and retrospective detail).
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
    from sqlalchemy import text
    from database import SessionLocal          # importing it loads backend/.env (APP_ENV, DATABASE_URL)
    if (os.environ.get("APP_ENV") or "").strip().lower() != "production":
        sys.exit("APP_ENV is not 'production' in this process: refusing (would read the dev namespace).")
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


def _fetch_fresh_rows():
    """cache_key, updated_at, value length for the earnings caches and scans. SELECT-only, READ ONLY txn."""
    from sqlalchemy import text
    from database import SessionLocal
    if (os.environ.get("APP_ENV") or "").strip().lower() != "production":
        sys.exit("APP_ENV is not 'production': refusing.")
    db = SessionLocal()
    try:
        db.execute(text("SET TRANSACTION READ ONLY"))
        q = ("SELECT cache_key, updated_at, length(value) FROM swr_cache WHERE cache_key LIKE 'earn:equity:%' "
             "OR cache_key LIKE 'earn:trade:default:%' OR cache_key LIKE 'gemini:hitrate:%' "
             "OR cache_key IN ('v3:spy15d','kimi:market') OR cache_key ~ '(^|:)scan(:|$)'")
        return [(k, float(ts), int(n)) for k, ts, n in db.execute(text(q)).all()]
    finally:
        db.rollback()
        db.close()


def fresh_stage(days=15, reader=None, fetch=None):
    """How stale is the data behind the live scores? Age buckets for every name on the earnings calendar."""
    reader = reader or read_cache
    fetch = fetch or _fetch_fresh_rows
    key = SCAN_KEYS["claude"].format(d=days)
    got = reader([key]).get(key)
    if not got:
        return {"status": "NO_CACHED_SCAN", "key": key}
    names = [(r.get("symbol") or "").upper() for r in (got[1].get("rows") or [])]
    now = time.time()
    held = {k: (ts, n) for k, ts, n in fetch()}
    edges = [(6, "<6h"), (24, "6-24h"), (48, "24-48h"), (96, "48-96h"), (1e9, ">96h")]

    def bucket(ts):
        if ts is None:
            return "missing"
        h = (now - ts) / 3600.0
        return next(lbl for e, lbl in edges if h < e)

    out = {"generated_at": _iso(now), "calendar_names": len(names), "age_buckets": {}, "scans": {}, "market": {}}
    for kind, tmpl in (("equity", "earn:equity:{s}"), ("trade", "earn:trade:default:{s}"), ("gemini_hitrate", "gemini:hitrate:{s}")):
        counts = {}
        for s in names:
            b = bucket((held.get(tmpl.format(s=s)) or (None, 0))[0])
            counts[b] = counts.get(b, 0) + 1
        out["age_buckets"][kind] = counts
    for m, tmpl in SCAN_KEYS.items():
        v = held.get(tmpl.format(d=days))
        out["scans"][m] = {"present": v is not None, "age_h": None if v is None else round((now - v[0]) / 3600.0, 2),
                           "bytes": None if v is None else v[1]}
    for k in ("v3:spy15d", "kimi:market"):
        v = held.get(k)
        out["market"][k] = {"present": v is not None, "age_h": None if v is None else round((now - v[0]) / 3600.0, 2)}
    return out


# ---- snapshots stage -----------------------------------------------------------------------------
# Provisional release windows in America/New_York (NOT confirmed release timestamps).
BMO_EARLIEST = "06:00"     # before this on the report day: before a Before-Open release
BMO_LATEST = "09:30"       # after this: after the open, so after the release; between = ambiguous
AMC_RELEASE = "16:00"      # before this on the report day: before an After-Close release

_CLASSIFIED = """
WITH x AS (
  SELECT model, symbol, earnings_date, report_timing, snapshot_date, predicted_at, signal, score,
         evaluated_at, correct, actual_move_pct,
         (predicted_at AT TIME ZONE 'America/New_York') AS pa_et,
         (predicted_at = (date_trunc('day', predicted_at AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')) AS midnight_stamp
  FROM model_predictions WHERE earnings_date IS NOT NULL
), c AS (
  SELECT *,
    CASE
      WHEN midnight_stamp THEN 'RETROSPECTIVE'
      WHEN pa_et::date > earnings_date THEN 'AFTER_RELEASE'
      WHEN pa_et::date < earnings_date THEN 'BEFORE_RELEASE'
      WHEN report_timing ILIKE '%before%' OR report_timing ILIKE 'bmo%' OR report_timing ILIKE '%pre%' THEN
        CASE WHEN pa_et::time < TIME '06:00' THEN 'BEFORE_RELEASE'
             WHEN pa_et::time < TIME '09:30' THEN 'AMBIGUOUS' ELSE 'AFTER_RELEASE' END
      WHEN report_timing ILIKE '%after%' OR report_timing ILIKE 'amc%' OR report_timing ILIKE '%post%' THEN
        CASE WHEN pa_et::time < TIME '16:00' THEN 'BEFORE_RELEASE' ELSE 'AFTER_RELEASE' END
      ELSE 'AMBIGUOUS' END AS cls
  FROM x)
"""

SNAP_QUERIES = {
    "columns": "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'model_predictions' ORDER BY ordinal_position",
    "totals": "SELECT COUNT(*) rows_total, COUNT(*) FILTER (WHERE earnings_date IS NULL) no_earnings_date, "
              "COUNT(*) FILTER (WHERE report_timing IS NULL OR report_timing = '') no_report_timing, "
              "COUNT(*) FILTER (WHERE score IS NULL) no_score FROM model_predictions",
    "by_model_class": _CLASSIFIED + "SELECT model, cls, COUNT(*) n, COUNT(*) FILTER (WHERE evaluated_at IS NOT NULL) evaluated, "
                      "COUNT(*) FILTER (WHERE evaluated_at IS NULL) pending, COALESCE(SUM(correct),0) correct "
                      "FROM c GROUP BY model, cls ORDER BY model, cls",
    "by_model": _CLASSIFIED + "SELECT model, COUNT(*) predictions, COUNT(DISTINCT (symbol, earnings_date)) events, "
                "COUNT(*) FILTER (WHERE evaluated_at IS NOT NULL) evaluated, COUNT(*) FILTER (WHERE evaluated_at IS NULL) pending, "
                "COUNT(*) FILTER (WHERE cls = 'RETROSPECTIVE') retrospective, COUNT(*) FILTER (WHERE cls = 'AFTER_RELEASE') after_release, "
                "COUNT(*) FILTER (WHERE cls = 'AMBIGUOUS') ambiguous, COUNT(*) FILTER (WHERE cls = 'BEFORE_RELEASE') before_release, "
                "MIN(predicted_at) first_predicted_at, MAX(predicted_at) last_predicted_at FROM c GROUP BY model ORDER BY model",
    "records": _CLASSIFIED + "SELECT symbol, earnings_date, model, report_timing, snapshot_date, "
               "to_char(pa_et, 'YYYY-MM-DD HH24:MI:SS') captured_et, cls, signal, score, "
               "(evaluated_at IS NOT NULL) evaluated, actual_move_pct, correct FROM c WHERE symbol IN ('AMZN', 'EAT', 'GRMN') "
               "ORDER BY symbol, earnings_date DESC, model, snapshot_date DESC LIMIT 1200",
    "claudepro_keys": "SELECT cache_key, length(value) bytes, updated_at FROM swr_cache WHERE cache_key LIKE 'claudepro:%' ORDER BY cache_key",
    "large_values": "SELECT COUNT(*) FILTER (WHERE length(value) > 1400000) over_1_4mb, COUNT(*) FILTER (WHERE length(value) > 1500000) over_cap_1_5mb, "
                    "MAX(length(value)) largest FROM swr_cache",
    "moves_by_key": "SELECT cache_key, CASE WHEN json_typeof((value::json)->'historical_moves') = 'array' "
                    "THEN json_array_length((value::json)->'historical_moves') END n FROM swr_cache WHERE cache_key LIKE 'earn:trade:default:%'",
}


def _run_ro(sql, params=None):
    from sqlalchemy import text
    from database import SessionLocal
    db = SessionLocal()
    try:
        db.execute(text("SET TRANSACTION READ ONLY"))
        return [dict(r._mapping) for r in db.execute(text(sql), params or {})]
    finally:
        db.rollback()
        db.close()


def snapshots_stage(days=15, reader=None, run=None, require_prod=True):
    """Prediction snapshot audit. Classifications use PROVISIONAL BMO/AMC windows, not release timestamps."""
    if require_prod:
        import database  # noqa: F401  (importing it loads backend/.env)
        if (os.environ.get("APP_ENV") or "").strip().lower() != "production":
            sys.exit("APP_ENV is not 'production': refusing.")
    run = run or _run_ro
    reader = reader or read_cache
    out = {"generated_at": _iso(time.time()), "provisional_windows_et": {"before_open_before": BMO_EARLIEST,
           "before_open_ambiguous_until": BMO_LATEST, "after_close_release": AMC_RELEASE},
           "note": "Classes are estimates; no confirmed release timestamps exist in the database.", "tables": {}}
    for k, q in SNAP_QUERIES.items():
        if k == "moves_by_key":
            continue
        try:
            out["tables"][k] = run(q)
        except Exception as exc:  # noqa: BLE001
            out["tables"][k] = {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}
    # Historical earnings-moves distribution over the calendar names.
    key = SCAN_KEYS["claude"].format(d=days)
    got = reader([key]).get(key)
    names = [(r.get("symbol") or "").upper() for r in ((got[1] if got else {}).get("rows") or [])]
    try:
        rows = {r["cache_key"][len("earn:trade:default:"):]: r["n"] for r in run(SNAP_QUERIES["moves_by_key"])}
        dist = {}
        for s in names:
            lbl = "no trade cache" if s not in rows else "no moves list" if rows[s] is None else str(rows[s])
            dist[lbl] = dist.get(lbl, 0) + 1
        out["moves_distribution_calendar_names"] = {"calendar_names": len(names), "n_moves": dict(sorted(dist.items()))}
    except Exception as exc:  # noqa: BLE001
        out["moves_distribution_calendar_names"] = {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}
    cols = {c["column_name"] for c in (out["tables"].get("columns") or []) if isinstance(c, dict)}
    out["unverifiable"] = {
        "input_snapshot_columns": sorted(cols & {"inputs", "params", "payload", "evidence", "input_snapshot"}),
        "coverage_column": sorted(cols & {"coverage", "coverage_pct"}),
        "release_timestamp_column": sorted(cols & {"released_at", "release_ts", "announced_at"}),
        "tier_column": sorted(cols & {"tier"}),
        "conclusion": ("Rows hold only model, symbol, dates, timing label, signal and score. Input values, data coverage, "
                       "input ages and the actual release time are not stored, so no prediction can be independently "
                       "re-derived or confirmed pre-release from the database alone."),
    }
    return out


# ---- integrity stage -----------------------------------------------------------------------------
# One clearly defined prediction per (model, symbol, earnings event), by three definitions:
#   verified_last : the LAST genuine BEFORE_RELEASE snapshot (strict pre-release, real capture timestamp)
#   verified_first: the EARLIEST genuine BEFORE_RELEASE snapshot (longest lead time)
#   app_board     : what the app's scoreboard uses today = latest snapshot on or before the earnings date,
#                   of ANY class (can be post-release or retrospective)
#   all_rows      : every row counted (shows how repeated daily rows inflate the sample)
# Retrospective, ambiguous and post-release rows are never part of the verified_* definitions.

_BASE2 = """
WITH x AS (
  SELECT id, model, symbol, earnings_date, report_timing, snapshot_date, predicted_at, signal, score,
         evaluated_at, correct, actual_move_pct, actual_direction,
         (predicted_at AT TIME ZONE 'America/New_York') AS pa_et,
         (predicted_at = (date_trunc('day', predicted_at AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')) AS midnight_stamp
  FROM {SRC} WHERE earnings_date IS NOT NULL
), c AS (
  SELECT *,
    CASE
      WHEN midnight_stamp THEN 'RETROSPECTIVE'
      WHEN pa_et::date > earnings_date THEN 'AFTER_RELEASE'
      WHEN pa_et::date < earnings_date THEN 'BEFORE_RELEASE'
      WHEN report_timing ILIKE '%before%' OR report_timing ILIKE 'bmo%' OR report_timing ILIKE '%pre%' THEN
        CASE WHEN pa_et::time < TIME '06:00' THEN 'BEFORE_RELEASE'
             WHEN pa_et::time < TIME '09:30' THEN 'AMBIGUOUS' ELSE 'AFTER_RELEASE' END
      WHEN report_timing ILIKE '%after%' OR report_timing ILIKE 'amc%' OR report_timing ILIKE '%post%' THEN
        CASE WHEN pa_et::time < TIME '16:00' THEN 'BEFORE_RELEASE' ELSE 'AFTER_RELEASE' END
      ELSE 'AMBIGUOUS' END AS cls
  FROM x
), ev_last AS (
  SELECT DISTINCT ON (model, symbol, earnings_date) * FROM c WHERE cls = 'BEFORE_RELEASE'
  ORDER BY model, symbol, earnings_date, predicted_at DESC
), ev_first AS (
  SELECT DISTINCT ON (model, symbol, earnings_date) * FROM c WHERE cls = 'BEFORE_RELEASE'
  ORDER BY model, symbol, earnings_date, predicted_at ASC
), ev_board AS (
  SELECT DISTINCT ON (model, symbol, earnings_date) * FROM c WHERE snapshot_date <= earnings_date
  ORDER BY model, symbol, earnings_date, snapshot_date DESC
)
"""

_OUTCOME_COLS = ("COUNT(*) events, "
                 "COUNT(*) FILTER (WHERE actual_move_pct IS NOT NULL) graded, "
                 "COUNT(*) FILTER (WHERE evaluated_at IS NOT NULL AND actual_move_pct IS NULL) unverifiable, "
                 "COUNT(*) FILTER (WHERE evaluated_at IS NULL) pending, "
                 "COALESCE(SUM(correct) FILTER (WHERE actual_move_pct IS NOT NULL), 0) correct")
_TODAY_ET = "(now() AT TIME ZONE 'America/New_York')::date"

INTEGRITY_QUERIES = {
    "accuracy": ("SELECT 'verified_last' def, model, signal, " + _OUTCOME_COLS + " FROM ev_last GROUP BY model, signal "
                 "UNION ALL SELECT 'verified_first', model, signal, " + _OUTCOME_COLS + " FROM ev_first GROUP BY model, signal "
                 "UNION ALL SELECT 'app_board', model, signal, " + _OUTCOME_COLS + " FROM ev_board GROUP BY model, signal "
                 "UNION ALL SELECT 'all_rows', model, signal, " + _OUTCOME_COLS + " FROM c GROUP BY model, signal "
                 "ORDER BY 1, 2, 3"),
    "board_composition": ("SELECT model, cls, COUNT(*) events, COUNT(*) FILTER (WHERE actual_move_pct IS NOT NULL) graded "
                          "FROM ev_board GROUP BY model, cls ORDER BY model, cls"),
    "event_coverage": ("SELECT model, COUNT(*) reported_events, COUNT(*) FILTER (WHERE has_before) with_genuine_before, "
                       "COUNT(*) FILTER (WHERE NOT has_before AND has_after) only_post_release, "
                       "COUNT(*) FILTER (WHERE NOT has_before AND NOT has_after AND has_retro) only_retrospective "
                       "FROM (SELECT model, symbol, earnings_date, BOOL_OR(cls = 'BEFORE_RELEASE') has_before, "
                       "BOOL_OR(cls = 'AFTER_RELEASE') has_after, BOOL_OR(cls = 'RETROSPECTIVE') has_retro FROM c "
                       "WHERE earnings_date < " + _TODAY_ET + " GROUP BY 1, 2, 3) q GROUP BY model ORDER BY model"),
    "grading_by_timing": ("SELECT model, report_timing, COUNT(*) events, "
                          "COUNT(*) FILTER (WHERE actual_move_pct IS NOT NULL) graded, "
                          "COUNT(*) FILTER (WHERE evaluated_at IS NOT NULL AND actual_move_pct IS NULL) unverifiable, "
                          "COUNT(*) FILTER (WHERE evaluated_at IS NULL) pending, "
                          "COUNT(*) FILTER (WHERE report_timing ILIKE '%after%' AND evaluated_at IS NOT NULL AND "
                          "evaluated_at < (((earnings_date + 1)::timestamp + interval '21 hours') AT TIME ZONE 'UTC')) amc_evaluated_before_reaction_close "
                          "FROM ev_last WHERE earnings_date < " + _TODAY_ET + " GROUP BY model, report_timing ORDER BY model, report_timing"),
    "base_rates": ("SELECT actual_direction, COUNT(*) n FROM ev_last WHERE model = 'claude_new' AND actual_move_pct IS NOT NULL "
                   "GROUP BY actual_direction ORDER BY actual_direction"),
    "snapshots_per_event": ("SELECT model, COUNT(*) events, ROUND(AVG(n), 2) avg_snapshots, MAX(n) max_snapshots FROM "
                            "(SELECT model, symbol, earnings_date, COUNT(*) n FROM c WHERE cls = 'BEFORE_RELEASE' GROUP BY 1, 2, 3) q "
                            "GROUP BY model ORDER BY model"),
    "repeated_scores": ("SELECT model, COUNT(*) transitions, COUNT(*) FILTER (WHERE score = prev_score) same_score, "
                        "COUNT(*) FILTER (WHERE signal = prev_signal) same_signal, COUNT(*) FILTER (WHERE signal <> prev_signal) flipped "
                        "FROM (SELECT model, score, signal, LAG(score) OVER w prev_score, LAG(signal) OVER w prev_signal FROM c "
                        "WHERE cls = 'BEFORE_RELEASE' WINDOW w AS (PARTITION BY model, symbol, earnings_date ORDER BY predicted_at)) q "
                        "WHERE prev_signal IS NOT NULL GROUP BY model ORDER BY model"),
    "after_release_detail": ("SELECT snapshot_date, to_char(pa_et, 'HH24') capture_hour_et, report_timing, COUNT(*) rows, "
                             "COUNT(DISTINCT model) models, COUNT(DISTINCT symbol) symbols FROM c WHERE cls = 'AFTER_RELEASE' "
                             "GROUP BY 1, 2, 3 ORDER BY 1, 2, 3"),
    "retrospective_detail": ("SELECT model, MIN(snapshot_date) first_snapshot, MAX(snapshot_date) last_snapshot, COUNT(*) rows, "
                             "COUNT(DISTINCT (symbol, earnings_date)) events, COUNT(*) FILTER (WHERE score IS NULL) no_score, "
                             "COUNT(*) FILTER (WHERE report_timing IS NULL OR report_timing = '') no_timing, "
                             "COUNT(*) FILTER (WHERE evaluated_at IS NOT NULL) evaluated FROM c WHERE cls = 'RETROSPECTIVE' "
                             "GROUP BY model ORDER BY model"),
    "date_sanity": ("SELECT model, MIN(snapshot_date) first_snapshot, MAX(snapshot_date) last_snapshot, MIN(earnings_date) first_event, "
                    "MAX(earnings_date) last_event, COUNT(*) FILTER (WHERE snapshot_date > earnings_date) snapshot_after_event, "
                    "COUNT(*) FILTER (WHERE snapshot_date < DATE '2026-10-13') before_launch FROM c GROUP BY model ORDER BY model"),
    "capture_runs": ("SELECT predicted_at, to_char(predicted_at AT TIME ZONE 'America/New_York', 'YYYY-MM-DD HH24:MI:SS') captured_et, "
                     "COUNT(*) rows, COUNT(DISTINCT model) models FROM c WHERE cls <> 'RETROSPECTIVE' "
                     "GROUP BY predicted_at ORDER BY predicted_at"),
    "source_tables": ("SELECT table_name, column_name FROM information_schema.columns WHERE table_name IN "
                      "('ped_scores','earnings_signal_snapshots','earnings_calendar','model_backtests','earnings_reactions',"
                      "'earnings_signal_history','score_snapshots') ORDER BY table_name, ordinal_position"),
    "scan_keys": ("SELECT cache_key, length(value) bytes, updated_at FROM swr_cache WHERE cache_key ~ '(^|:)scan(:|$)' "
                  "OR cache_key LIKE 'claudepro:%' OR cache_key LIKE 'allmodels:%' ORDER BY cache_key"),
}
_COUNT_TABLES = ("ped_scores", "earnings_signal_snapshots", "model_backtests", "score_snapshots")


def wilson(ok, n, z=1.96):
    """95% Wilson interval, in percent, for a hit rate (so small samples are not over-read)."""
    if not n:
        return None
    p = ok / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return round((centre - half) * 100, 1), round((centre + half) * 100, 1)


def integrity_stage(run=None, require_prod=True, src="model_predictions", now=None):
    """Prediction-history integrity audit. SELECT-only; definitions documented above."""
    if require_prod:
        import database  # noqa: F401  (importing it loads backend/.env)
        if (os.environ.get("APP_ENV") or "").strip().lower() != "production":
            sys.exit("APP_ENV is not 'production': refusing.")
    run = run or _run_ro
    now = now or time.time()
    base = _BASE2.replace("{SRC}", src)
    out = {"generated_at": _iso(now), "definitions": {
        "verified_last": "last genuine BEFORE_RELEASE snapshot per (model, symbol, earnings_date)",
        "verified_first": "earliest genuine BEFORE_RELEASE snapshot per event",
        "app_board": "latest snapshot on/before the earnings date, any class (what the app scoreboard uses today)",
        "all_rows": "every row (shows inflation from repeated daily snapshots)"},
        "note": "BEFORE/AFTER/AMBIGUOUS use provisional ET windows; no confirmed release timestamps exist in the database.",
        "tables": {}}
    for k, q in INTEGRITY_QUERIES.items():
        try:
            if k in ("source_tables", "scan_keys"):
                out["tables"][k] = run(q)
            else:
                out["tables"][k] = run(base + q)
        except Exception as exc:  # noqa: BLE001
            out["tables"][k] = {"error": f"{type(exc).__name__}: {str(exc)[:240]}"}
    counts = {}
    for t in _COUNT_TABLES:
        try:
            counts[t] = run(f"SELECT COUNT(*) n FROM {t}")[0]["n"]
        except Exception as exc:  # noqa: BLE001
            counts[t] = f"absent/err: {type(exc).__name__}"
    out["table_row_counts"] = counts
    sk = out["tables"].get("scan_keys")
    if isinstance(sk, list):
        for r in sk:
            r["age_h"] = round((now - float(r["updated_at"])) / 3600.0, 2)
    # accuracy tables with sample sizes and intervals
    acc = {}
    for r in (out["tables"].get("accuracy") or []):
        if not isinstance(r, dict) or r.get("signal") not in ("BUY", "SELL", "NEUTRAL"):
            continue
        key = f'{r["def"]}|{r["model"]}'
        a = acc.setdefault(key, {})
        g, ok = int(r["graded"]), int(r["correct"])
        a[r["signal"]] = {"events": int(r["events"]), "graded": g, "unverifiable": int(r["unverifiable"]),
                          "pending": int(r["pending"]), "correct": ok,
                          "accuracy_pct": round(ok / g * 100, 1) if g else None, "ci95": wilson(ok, g)}
    out["accuracy_summary"] = acc
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
    ap.add_argument("--stage", choices=["pick", "cache", "fresh", "snapshots", "integrity", "raw", "db"], required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    syms = [s for s in a.symbols.replace(" ", "").split(",") if s]
    if a.stage in ("cache", "raw") and not syms:
        sys.exit("--symbols is required for this stage")
    if a.stage == "raw" and len(syms) > 12:
        sys.exit("raw stage is limited to 12 symbols (provider budget)")
    res = {"pick": pick_stage, "fresh": fresh_stage, "snapshots": snapshots_stage, "integrity": integrity_stage, "cache": lambda: cache_stage(syms), "raw": lambda: raw_stage(syms), "db": db_stage}[a.stage]()
    Path(a.out).write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    print(f"wrote {a.out}")
    if a.stage == "pick":
        print("scan cached", res.get("scan_cached_at"), "age_h", res.get("scan_age_h"), "names", res.get("n"))
        for r in sorted(res.get("rows", []), key=lambda r: (r.get("days_to") is None, r.get("days_to"))):
            print(f"{r['symbol']:6s} {str(r['date']):10s} d{r['days_to']} {str(r.get('report_timing') or r.get('time_label') or ''):10s} "
                  f"score {r['score']} {r['signal']} cov {r['coverage']}")
    if a.stage == "integrity":
        t_ = res["tables"]

        def show(title, key, fmt=None):
            print("\n" + title)
            v = t_.get(key)
            if not isinstance(v, list):
                print("  ", v)
                return
            for r in v:
                print("  ", fmt(r) if fmt else {k: (str(x)[:19] if "updated" in k or k == "predicted_at" else x) for k, x in r.items()})
        print("definitions:", json.dumps(res["definitions"], indent=1))
        print("table row counts:", res["table_row_counts"])
        show("EVENT COVERAGE (reported events per model)", "event_coverage")
        show("SNAPSHOTS PER EVENT (genuine before-release rows)", "snapshots_per_event")
        show("REPEATED SCORES between consecutive daily snapshots", "repeated_scores")
        show("BOARD COMPOSITION (class of the snapshot the app scoreboard grades)", "board_composition")
        show("GRADING BY REPORT TIMING (verified_last events)", "grading_by_timing")
        show("BASE RATES of actual direction (claude_new graded events)", "base_rates")
        print("\nACCURACY (BUY/SELL directional; NEUTRAL = flat-band match). def | model | signal: graded n, correct, acc%, 95% CI, unverifiable, pending")
        for key in sorted(res["accuracy_summary"]):
            for sig in ("BUY", "SELL", "NEUTRAL"):
                v = res["accuracy_summary"][key].get(sig)
                if v:
                    print(f"  {key:28s} {sig:8s} events={v['events']:<6} graded={v['graded']:<5} correct={v['correct']:<5} acc={v['accuracy_pct']}  ci95={v['ci95']}  unverifiable={v['unverifiable']} pending={v['pending']}")
        show("AFTER_RELEASE detail (provisional)", "after_release_detail")
        show("RETROSPECTIVE detail", "retrospective_detail")
        show("DATE SANITY", "date_sanity")
        show("CAPTURE RUNS (distinct predicted_at, non-retrospective)", "capture_runs",
             lambda r: f"{r['captured_et']} ET  rows={r['rows']} models={r['models']}")
        show("SCAN CACHE KEYS (Claude Pro and others)", "scan_keys",
             lambda r: f"{r['cache_key']:28s} bytes={r['bytes']:<9} age_h={r['age_h']}")
        tabs = {}
        for r in (t_.get("source_tables") or []):
            tabs.setdefault(r["table_name"], []).append(r["column_name"])
        print("\nSOURCE TABLE COLUMNS:", tabs)
    if a.stage == "snapshots":
        t_ = res["tables"]
        print("windows (ET, provisional):", res["provisional_windows_et"])
        print("columns:", [c["column_name"] for c in t_["columns"]] if isinstance(t_["columns"], list) else t_["columns"])
        print("totals:", t_["totals"])
        print("\nPER MODEL:")
        for r in (t_["by_model"] if isinstance(t_["by_model"], list) else [t_["by_model"]]):
            print(" ", {k: (str(v)[:19] if "predicted_at" in k else v) for k, v in r.items()})
        print("\nPER MODEL x CLASS (n / evaluated / pending / correct):")
        for r in (t_["by_model_class"] if isinstance(t_["by_model_class"], list) else []):
            print(f"  {r['model']:11s} {r['cls']:14s} n={r['n']:<6} evaluated={r['evaluated']:<6} pending={r['pending']:<6} correct={r['correct']}")
        print("\nRECORDS (AMZN, EAT, GRMN) - newest first, max 70 per symbol:")
        seen = {}
        for r in (t_["records"] if isinstance(t_["records"], list) else []):
            seen[r["symbol"]] = seen.get(r["symbol"], 0) + 1
            if seen[r["symbol"]] <= 70:
                print(f"  {r['symbol']:5s} earn {r['earnings_date']} {str(r['report_timing'] or ''):12s} {r['model']:11s} snap {r['snapshot_date']} "
                      f"cap_et {r['captured_et']} {r['cls']:14s} {r['signal']:7s} {r['score']} eval={r['evaluated']} move={r['actual_move_pct']} ok={r['correct']}")
        print("\nCLAUDE PRO cache keys:", t_["claudepro_keys"])
        print("LARGE swr values:", t_["large_values"])
        print("\nEARNINGS-MOVES DISTRIBUTION:", res["moves_distribution_calendar_names"])
        print("\nUNVERIFIABLE:", res["unverifiable"])
    if a.stage == "fresh":
        print("calendar names:", res.get("calendar_names"))
        for k, v in res.get("age_buckets", {}).items():
            print(f"  {k:15s}", v)
        for k, v in res.get("scans", {}).items():
            print(f"  scan {k:11s}", v)
        print("  market", res.get("market"))
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
