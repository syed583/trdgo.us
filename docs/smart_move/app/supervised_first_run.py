"""SUPERVISED FIRST LIVE RUN of the Smart Move refresh service (approved by the owner; run by hand, once, with someone watching).

    python supervised_first_run.py --store-dir <new empty folder> [--execute]

Without --execute it only checks and prints the plan (no request). With --execute it sends AT MOST 13 read-only requests through the governed service:
  1. calendar refresh: Unusual Whales premarket/afterhours for the next trading days (page 0, page 1 only when page 0 is full), one Finviz export, one Finnhub window  (at most 8)
  2. a one-symbol format probe (AAPL, SPY, XLK): daily candles x3, earnings history, volatility context, iv-rank  (6)
Authorization is set to 20 for this process only; the kill switch is released only inside this process and is engaged again (KILL file) when the run ends, so the normal
local server stays locked. Stops at the first provider error. No key or token is printed or written. Everything is stored with retrieval time and SHA-256.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))
ENV = Path(r"C:\Users\initi\Downloads\US-STOCKS\backend\.env")          # the owner's key file (git-ignored); only loaded into this process
CAP_TOTAL, AUTHORIZED = 13, 20


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--store-dir", required=True)
    ap.add_argument("--execute", action="store_true")
    a = ap.parse_args()
    store = Path(a.store_dir)
    if store.exists() and any(store.iterdir()):
        sys.exit("STOPPED: store folder is not empty (never reused)")
    from dotenv import load_dotenv
    load_dotenv(ENV)
    import smart_move_live as L
    import unusualwhales_service as uw
    import finnhub_service as fh
    import finviz_service as fz
    creds = {"UNUSUAL_WHALES": uw.configured(), "FINNHUB": bool(fh._key()), "FINVIZ": fz.configured()}
    print("credentials present (values never shown):", creds)
    cfg = L.LiveConfig(mode="LIVE", store_dir=store, refresh_enabled=True, authorized_daily=AUTHORIZED, spacing_s=2.0)
    svc = L.RefreshService(cfg, L.InfraFetcher() if a.execute else None, L.SystemClock())
    now = svc.clock.now()
    print("now (UTC):", now.isoformat(), "| trading days:", [str(d) for d in svc._trading_days(now)], "| authorization:", AUTHORIZED, "| cap this run:", CAP_TOTAL)
    print("blockers inside this process:", [b["code"] for b in svc.blockers(now)])
    if not a.execute:
        print("dry run: nothing sent")
        return
    if not all(creds.values()):
        sys.exit("STOPPED: a provider credential is missing")
    sent = {"n": 0}
    try:
        out = svc.run(scope="calendar", max_requests=8, manual=True)
        print("calendar run:", out)
        if out["status"] != "OK" or out["requests"] == 0:
            sys.exit("STOPPED: the calendar refresh did not complete")
        sent["n"] += out["requests"]
        exp = L.expected_session(svc.clock.now()).isoformat()
        stamp = svc.clock.now().strftime("%Y%m%dT%H%M%S")
        probe = [("ohlc", "SPY", {"timeframe": "3Y"}), ("ohlc", "XLK", {"timeframe": "3Y"}), ("ohlc", "AAPL", {"timeframe": "3Y"}), ("earnings", "AAPL", {"limit": 24}),
                 ("vol_context", "AAPL", {"date": exp}), ("iv_rank", "AAPL", {"date": exp})]
        for kind, sym, params in probe:
            if sent["n"] >= CAP_TOTAL:
                break
            rid = "probe-%s-%s-%s" % (kind, sym, stamp)
            body, sha, at = svc._call(kind, sym, params, rid)
            sent["n"] += 1
            if body is None:
                print("probe stopped at", kind, sym, svc.store.state().get("last_error"))
                break
            svc._ingest_evidence(kind, sym, params, body, sha, at, rid)
            print("probe ok:", kind, sym, "bytes", len(body))
        rows = L.LiveScorer(svc).score_all(svc.clock.now(), persist=True)
        a_row = next((r for r in rows if r["symbol"] == "AAPL"), None)
        feed = svc.feed()
        report = {"retrieved_utc": now.isoformat(), "requests_sent_total": len(svc.store.read("requests")), "requests_by_provider": feed["requests_today_by_provider"],
                  "calendar_symbols_scored": len(rows), "aapl": None if not a_row else {k: a_row[k] for k in ("earnings_date", "kind", "available_points", "blocked_by", "missing_factors")},
                  "aapl_engine": None if not a_row else a_row["engine"], "aapl_calendar": None if not a_row else a_row["calendar"], "freshness": feed["freshness"],
                  "status_counts": {}, "ledger_denials": [e["type"] for e in svc.store.read("ledger") if e["type"].startswith(("DENIED", "STOP", "PROVIDER"))]}
        for r in rows:
            report["status_counts"][r["calendar"]["status_now"]] = report["status_counts"].get(r["calendar"]["status_now"], 0) + 1
        (store / "first_run_report.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
        print(json.dumps(report, indent=1, default=str))
    finally:
        svc.engage_kill_switch("first supervised run finished")
        print("kill switch engaged again; requests sent:", len(svc.store.read("requests")))


if __name__ == "__main__":
    main()
