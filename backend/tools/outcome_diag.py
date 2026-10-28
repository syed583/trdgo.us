"""READ-ONLY outcome-data diagnostic for the production VPS (written locally, not run there).

Question: why does the Phase 2A preview find no outcomes, and what outcome data does production actually hold?
It answers, from stored data only (SELECTs in READ ONLY transactions, no provider calls, network blocked while
computing, writes only --out):

  1. Population reconciliation: the events behind "380 missing-price reported events" versus "309 missing
     outcomes per main model" (different populations, shown event by event category).
  2. For every reported event whose reaction session has closed (new rules), which STORED outcome sources exist:
     the legacy outcome columns, the cached earnings-move history (earn:trade:default:*), the calendar table.
  3. A sample audit of completed Before Open / After Close events: expected trading dates and ready time under the
     new rules, what the legacy grader stored and when, the cached move and the cache build time, the calendar row,
     the nearest stored price points (score_snapshots), and the reason each outcome was not available.
  4. Symbol hygiene and an inventory of tables that hold price-like columns.

The corrected modules (market_calendar, earnings_outcomes, earnings_scoreboard) are embedded and loaded from memory;
their database / provider functions are switched off.

Usage (from C:\\apps\\us-stock-reader\\backend, production venv, APP_ENV=production in backend\\.env):
    python <path>\\outcome_diag.py --out C:\\audit-reports\\readonly_verify\\outcome_diag.json
"""
from __future__ import annotations

import argparse
import base64
import gzip
import json
import os
import sys
import types
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

_EMBEDDED = {
    "market_calendar": (
        "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"),
    "earnings_outcomes": (
        "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"),
    "earnings_scoreboard": (
        "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"
        "TJ3kRpknvSyTOWzsikzvhM9QbONfRS1cCDI7AAA="),
}


def load_embedded():
    for name in ("market_calendar", "earnings_outcomes", "earnings_scoreboard"):
        src = gzip.decompress(base64.b64decode(_EMBEDDED[name])).decode("utf-8")
        mod = types.ModuleType(name)
        mod.__file__ = f"<embedded {name}>"
        sys.modules[name] = mod
        exec(compile(src, mod.__file__, "exec"), mod.__dict__)
    eo, sb = sys.modules["earnings_outcomes"], sys.modules["earnings_scoreboard"]

    def _blocked(name):
        def f(*a, **k):
            raise RuntimeError(f"{name} is disabled in the read-only diagnostic")
        return f
    for fn in ("run", "ensure_table", "load_outcomes", "_save", "sync_legacy_columns", "events_to_review",
               "default_bars_fetcher"):
        setattr(eo, fn, _blocked("earnings_outcomes." + fn))
    for fn in ("scoreboard", "load_rows"):
        setattr(sb, fn, _blocked("earnings_scoreboard." + fn))
    return sys.modules["market_calendar"], eo, sb


class no_network:
    def __enter__(self):
        import socket
        self._real = socket.socket.connect

        def blocked(*a, **k):
            raise RuntimeError("network blocked by the read-only diagnostic")
        socket.socket.connect = blocked
        return self

    def __exit__(self, *exc):
        import socket
        socket.socket.connect = self._real


MODELS = ["claude_new", "claude_pro", "gemini", "grok", "chatgpt", "meta", "kimi", "ped"]

SQL = {
    "snaps": ("SELECT model, symbol, earnings_date, report_timing, snapshot_date, predicted_at, signal, "
              "actual_move_pct, actual_direction, correct, evaluated_at FROM model_predictions WHERE earnings_date IS NOT NULL"),
    "calendar": ("SELECT symbol, earnings_date, reporting_time, report_time, reaction_date, post_earnings_move_percent, "
                 "eps_actual, lifecycle, data_status, fetched_at, updated_at FROM earnings_calendar "
                 "WHERE earnings_date >= DATE '2026-10-13'"),
    "trade_cache": ("SELECT cache_key, updated_at, CASE WHEN left(value, 1) = '{' THEN (value::json)->'historical_moves' END AS hm "
                    "FROM swr_cache WHERE cache_key LIKE 'earn:trade:default:%'"),
    "score_prices": ("SELECT symbol, snapshot_date, price, created_at FROM score_snapshots "
                     "WHERE snapshot_date >= DATE '2026-10-13' AND price IS NOT NULL"),
    "price_columns": ("SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = 'public' AND "
                      "(column_name ILIKE '%close%' OR column_name IN ('price', 'last_price', 'entry_price', 'exit_price', 'spot')) "
                      "ORDER BY table_name, column_name"),
}


def _run_ro(sql):
    from sqlalchemy import text
    from database import SessionLocal
    db = SessionLocal()
    try:
        db.execute(text("SET TRANSACTION READ ONLY"))
        return [dict(r._mapping) for r in db.execute(text(sql))]
    finally:
        db.rollback()
        db.close()


def _d(v):
    if v is None or (isinstance(v, date) and not isinstance(v, datetime)):
        return v
    return v.date() if isinstance(v, datetime) else date.fromisoformat(str(v)[:10])


def _utc(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):                       # swr_cache.updated_at is epoch seconds (double precision)
        return datetime.fromtimestamp(float(v), tz=timezone.utc)
    if isinstance(v, str):
        v = datetime.fromisoformat(v.replace("Z", "+00:00"))
    return v if v.tzinfo else v.replace(tzinfo=timezone.utc)


def _iso(v):
    return None if v is None else v.astimezone(timezone.utc).isoformat(timespec="minutes")


def diagnose(snaps, cal, trades, prices, price_cols, now=None):
    from zoneinfo import ZoneInfo
    mc, eo, sb = load_embedded()
    now = now or datetime.now(timezone.utc)
    today_et = now.astimezone(ZoneInfo("America/New_York")).date()
    out = {"generated_at": _iso(now), "today_et": str(today_et)}

    # ---- trade-cache move history per symbol ----
    cached = {}
    for t in trades:
        sym = t["cache_key"].split(":")[-1]
        hm = t.get("hm")
        if isinstance(hm, str):
            try:
                hm = json.loads(hm)
            except ValueError:
                hm = None
        cached[sym] = (_utc(t["updated_at"]), hm if isinstance(hm, list) else [])
    out["trade_cache_symbols"] = len(cached)

    cal_by = {(c["symbol"], _d(c["earnings_date"])): c for c in cal}

    # ---- events from the scored models' snapshots ----
    ev = {}
    for r in snaps:
        key = (r["symbol"], _d(r["earnings_date"]))
        e = ev.setdefault(key, {"label": None, "classes": {}, "legacy_move": None, "legacy_dir": None,
                                "stamp": None, "stamp_label": None})
        if r["actual_move_pct"] is not None and e["legacy_move"] is None:
            e["legacy_move"] = float(r["actual_move_pct"])
            e["legacy_dir"] = r["actual_direction"]
        if r["model"] not in MODELS:
            continue
        if r["report_timing"]:
            e["label"] = r["report_timing"]
        cls = sb.classify(_d(r["earnings_date"]), r["report_timing"], _utc(r["predicted_at"]))
        e["classes"].setdefault(r["model"], set()).add(cls)
        if r["evaluated_at"] is not None and r["report_timing"]:
            st = _utc(r["evaluated_at"])
            if e["stamp"] is None or st < e["stamp"]:
                e["stamp"], e["stamp_label"] = st, r["report_timing"]
    ev = {k: v for k, v in ev.items() if v["classes"]}                # only events with a scored-model snapshot
    out["events_with_scored_snapshots"] = len(ev)

    def kind_of(key, e):
        c = cal_by.get(key) or {}
        return eo.timing_kind(e["label"]) or eo.timing_kind(c.get("reporting_time")) or eo.timing_kind(c.get("report_time"))

    info = {}
    for key, e in ev.items():
        sym, ed = key
        if ed > today_et:
            info[key] = {"phase": "not_reported", "kind": kind_of(key, e)}
            continue
        k = kind_of(key, e)
        if k is None:
            info[key] = {"phase": "timing_unknown", "kind": None}
            continue
        base_d, react_d = eo.reaction_window(ed, k)
        rdy = eo.ready_at(react_d)
        info[key] = {"phase": "ready" if now >= rdy else "window_open", "kind": k, "base": base_d, "react": react_d, "ready_at": rdy}

    def count(pred):
        return sum(1 for k, i in info.items() if pred(k, i))

    # ---- 1. reconciliation of the two populations ----
    ready = [k for k, i in info.items() if i["phase"] == "ready"]
    unknown = [k for k, i in info.items() if i["phase"] == "timing_unknown"]
    pop380 = set(ready) | set(unknown)
    elig = {m: {k for k in pop380 if "BEFORE_RELEASE" in ev[k]["classes"].get(m, ())} for m in MODELS}
    claude = elig["claude_new"]
    rest = pop380 - claude
    why = {"claude_new has only post-release snapshots": 0, "claude_new has only ambiguous/retrospective snapshots": 0,
           "no claude_new snapshot (other models only, e.g. Pre-Earnings Direction)": 0}
    for k in rest:
        cl = ev[k]["classes"].get("claude_new")
        if not cl:
            why["no claude_new snapshot (other models only, e.g. Pre-Earnings Direction)"] += 1
        elif "AFTER_RELEASE" in cl:
            why["claude_new has only post-release snapshots"] += 1
        else:
            why["claude_new has only ambiguous/retrospective snapshots"] += 1
    out["population"] = {
        "reported_events": count(lambda k, i: i["phase"] in ("ready", "window_open", "timing_unknown")),
        "ready_past_reaction_close": {"Before Open": count(lambda k, i: i["phase"] == "ready" and i["kind"] == "BMO"),
                                      "After Close": count(lambda k, i: i["phase"] == "ready" and i["kind"] == "AMC")},
        "window_open": {"Before Open": count(lambda k, i: i["phase"] == "window_open" and i["kind"] == "BMO"),
                        "After Close": count(lambda k, i: i["phase"] == "window_open" and i["kind"] == "AMC")},
        "timing_unknown": len(unknown), "not_reported_yet": count(lambda k, i: i["phase"] == "not_reported"),
        "union_ready_or_unknown (the '380')": len(pop380),
        "claude_new_eligible_in_that_union (the '309')": len(claude),
        "per_model_eligible_in_union": {m: len(v) for m, v in elig.items()},
        "union_events_not_eligible_for_claude_new": len(rest), "why_not_eligible": why}

    # ---- 2. stored outcome sources for completed events ----
    def sources(k):
        e, i = ev[k], info[k]
        sym, ed = k
        c = cal_by.get(k)
        hm = cached.get(sym, (None, []))[1]
        exact = [m for m in hm if _d(m.get("date")) == ed]
        near = [m for m in hm if m.get("date") and abs((_d(m.get("date")) - ed).days) == 1]
        return {"legacy_move": e["legacy_move"] is not None,
                "cached_move_exact": bool(exact), "cached_move_near_date": bool(near) and not exact,
                "calendar_move": bool(c and c.get("post_earnings_move_percent") is not None),
                "calendar_row": c is not None}

    tab = {}
    for lab, pred in (("Before Open", lambda i: i["kind"] == "BMO"), ("After Close", lambda i: i["kind"] == "AMC"),
                      ("timing unknown", lambda i: i["kind"] is None)):
        ks = [k for k in pop380 if pred(info[k])]
        s = {k: sources(k) for k in ks}
        tab[lab] = {
            "completed_events": len(ks),
            "legacy_outcome_stored": sum(v["legacy_move"] for v in s.values()),
            "cached_move_stored_exact_date": sum(v["cached_move_exact"] for v in s.values()),
            "cached_move_only_one_day_off": sum(v["cached_move_near_date"] for v in s.values()),
            "calendar_move_stored": sum(v["calendar_move"] for v in s.values()),
            "calendar_row_exists": sum(v["calendar_row"] for v in s.values()),
            "any_stored_outcome": sum(1 for v in s.values() if v["legacy_move"] or v["cached_move_exact"] or v["calendar_move"]),
            "no_stored_outcome_needs_price_fetch": sum(1 for v in s.values() if not (v["legacy_move"] or v["cached_move_exact"] or v["calendar_move"]))}
    out["stored_outcome_sources_for_completed_events"] = tab

    # ---- cross-check legacy vs cached moves ----
    pairs = []
    for k in pop380:
        sym, ed = k
        hm = cached.get(sym, (None, []))[1]
        ex = [m for m in hm if _d(m.get("date")) == ed]
        if ex and ev[k]["legacy_move"] is not None:
            pairs.append((abs(ev[k]["legacy_move"] - float(ex[0]["move_pct"])), k, ev[k]["legacy_move"], float(ex[0]["move_pct"])))
    pairs.sort(reverse=True)
    out["legacy_vs_cached_moves"] = {
        "events_with_both": len(pairs), "within_0.05pct": sum(1 for p in pairs if p[0] <= 0.05),
        "largest_differences": [{"symbol": p[1][0], "earnings_date": str(p[1][1]), "legacy": p[2], "cached": p[3]} for p in pairs[:8]]}

    # ---- 3. sample audit ----
    px = {}
    for p in prices:
        px.setdefault(p["symbol"], []).append((_d(p["snapshot_date"]), p["price"], _utc(p["created_at"])))

    def reason(k):
        e, i = ev[k], info[k]
        s = sources(k)
        bits = []
        if e["legacy_move"] is None:
            if e["stamp"] is not None and i.get("ready_at") and e["stamp"] < i["ready_at"]:
                bits.append(f"legacy grader stamped it {_iso(e['stamp'])} UTC, before the reaction session closed ({_iso(i['ready_at'])} UTC), and recorded no move")
            elif e["stamp"] is not None:
                bits.append("legacy grader stamped it after the close but recorded no move (price or report-date match missing)")
            else:
                bits.append("never evaluated by the legacy grader")
        else:
            bits.append("legacy move stored")
        bits.append("calendar row missing" if not s["calendar_row"] else
                    ("calendar move NULL" if not s["calendar_move"] else "calendar move stored"))
        if s["cached_move_exact"]:
            bits.append("cached earnings-move history has this event")
        elif s["cached_move_near_date"]:
            bits.append("cached history has an event one day off the calendar date")
        else:
            bits.append("no cached move for this date")
        return "; ".join(bits)

    def card(k):
        e, i = ev[k], info[k]
        sym, ed = k
        t_at, hm = cached.get(sym, (None, []))
        ex = [m for m in hm if _d(m.get("date")) == ed] or [m for m in hm if m.get("date") and abs((_d(m.get("date")) - ed).days) <= 1]
        pts = sorted(px.get(sym, []))
        near_pts = [p for p in pts if i.get("base") and abs((p[0] - i["base"]).days) <= 3 or i.get("react") and abs((p[0] - i["react"]).days) <= 3]
        return {"symbol": sym, "earnings_date": str(ed), "label": e["label"], "expected_base_close_date": str(i.get("base")),
                "expected_reaction_close_date": str(i.get("react")), "ready_at_utc": _iso(i.get("ready_at")),
                "legacy_move_pct": e["legacy_move"], "legacy_stamped_at_utc": _iso(e["stamp"]),
                "cached_move": ex[:1], "trade_cache_built_utc": _iso(t_at),
                "calendar": {k2: (str(v) if v is not None else None) for k2, v in (cal_by.get((sym, ed)) or {}).items()
                             if k2 in ("reporting_time", "reaction_date", "post_earnings_move_percent", "eps_actual", "lifecycle", "updated_at")} or None,
                "stored_prices_score_snapshots": [{"date": str(p[0]), "price": p[1], "stored_utc": _iso(p[2])} for p in near_pts[:6]],
                "why_no_new_outcome": reason(k)}

    def pick(pred, n=3):
        return [card(k) for k in sorted(pop380) if pred(k)][:n]
    out["sample_audit"] = {
        "BMO legacy graded": pick(lambda k: info[k]["kind"] == "BMO" and ev[k]["legacy_move"] is not None),
        "BMO legacy stamped, no move": pick(lambda k: info[k]["kind"] == "BMO" and ev[k]["legacy_move"] is None and ev[k]["stamp"] is not None),
        "AMC stamped early, cached move exists": pick(lambda k: info[k]["kind"] == "AMC" and ev[k]["legacy_move"] is None and sources(k)["cached_move_exact"]),
        "AMC stamped early, no stored move": pick(lambda k: info[k]["kind"] == "AMC" and ev[k]["legacy_move"] is None and not sources(k)["cached_move_exact"]),
        "AMC legacy graded (suspect)": pick(lambda k: info[k]["kind"] == "AMC" and ev[k]["legacy_move"] is not None),
        "timing unknown": pick(lambda k: info[k]["kind"] is None)}

    # ---- 4. hygiene ----
    syms = sorted({k[0] for k in ev})
    out["symbol_hygiene"] = {
        "event_symbols": len(syms), "non_alpha_symbols": [s for s in syms if not s.isalpha() or s != s.upper()][:20],
        "completed_events_whose_symbol_has_no_trade_cache": sum(1 for k in pop380 if k[0] not in cached)}
    tbls = {}
    for r in price_cols:
        tbls.setdefault(r["table_name"], []).append(r["column_name"])
    out["tables_with_price_like_columns"] = tbls
    out["calendar_table"] = {"rows_since_2026-10-13": len(cal), "with_move": sum(1 for c in cal if c.get("post_earnings_move_percent") is not None),
                             "with_eps_actual": sum(1 for c in cal if c.get("eps_actual") is not None)}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    if os.path.exists(os.path.join(os.getcwd(), "database.py")) and os.getcwd() not in sys.path:
        sys.path.insert(0, os.getcwd())
    import database  # noqa: F401  (loads backend/.env)
    if (os.environ.get("APP_ENV") or "").strip().lower() != "production":
        sys.exit("APP_ENV is not 'production': refusing.")
    data = {}
    for k, q in SQL.items():
        try:
            data[k] = _run_ro(q)
        except Exception as exc:  # noqa: BLE001
            print(f"query {k} failed: {type(exc).__name__}: {str(exc)[:120]}")
            data[k] = []
    with no_network():
        res = diagnose(data["snaps"], data["calendar"], data["trade_cache"], data["score_prices"], data["price_columns"])
    Path(a.out).write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    print("wrote", a.out)
    print("trade-cache symbols:", res["trade_cache_symbols"], "| events with scored snapshots:", res["events_with_scored_snapshots"], "| calendar table:", res["calendar_table"])
    print("\nPOPULATIONS")
    for k, v in res["population"].items():
        print("  ", k, ":", v)
    print("\nSTORED OUTCOME SOURCES FOR COMPLETED EVENTS (reaction session closed, or timing unknown)")
    for k, v in res["stored_outcome_sources_for_completed_events"].items():
        print("  ", k, v)
    print("\nLEGACY vs CACHED MOVES:", res["legacy_vs_cached_moves"])
    print("\nSAMPLE AUDIT")
    for cat, cards in res["sample_audit"].items():
        print(f"-- {cat} ({len(cards)})")
        for c in cards:
            print("  ", json.dumps(c, default=str))
    print("\nSYMBOL HYGIENE:", res["symbol_hygiene"])
    print("TABLES WITH PRICE-LIKE COLUMNS:", res["tables_with_price_like_columns"])


if __name__ == "__main__":
    main()
