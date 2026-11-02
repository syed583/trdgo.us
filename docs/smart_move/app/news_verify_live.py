"""Controlled live verification of /api/news/headlines: at most MAX_REQUESTS read-only requests, one per ticker, page 0, no retry, 2 s spacing, own output folder.

    python news_verify_live.py --out-dir <NEW folder> --tickers AAPL,NVDA,MSFT,CE

Goes through the existing governed path (RefreshService._call: authorization cap, spacing, 401/403/429 stop, ledger, kill switch) with the existing InfraFetcher. Credentials are never printed or stored.
Nothing else is requested: no calendar, no paging, no other endpoint. Results go into a separate store, never into a production prediction store.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "backend"))
ENV = Path(r"C:\Users\initi\Downloads\US-STOCKS\backend\.env")
UTC = timezone.utc
MAX_REQUESTS = 4


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--tickers", required=True)
    ap.add_argument("--companies", default="", help="optional SYMBOL=Company Name;... for the research classifier")
    a = ap.parse_args(argv)
    tickers = [t.strip().upper() for t in a.tickers.split(",") if t.strip()]
    if len(tickers) > MAX_REQUESTS:
        print("STOPPED: more than %d tickers" % MAX_REQUESTS)
        return 2
    out = Path(a.out_dir)
    if out.exists():
        print("STOPPED: output folder exists (a verification never reuses a folder)")
        return 3
    (out / "store").mkdir(parents=True)
    from dotenv import load_dotenv
    load_dotenv(ENV)
    import smart_move_live as L
    import smart_move_news as NW
    import smart_move_v2_collect as C
    import unusualwhales_service as uw
    if not uw.configured():
        print("STOPPED: Unusual Whales credential missing (value never shown)")
        return 4
    captured = {}

    class RecordingFetcher(L.InfraFetcher):
        """Same fetcher; additionally keeps the integration's own status string (never the key) for the report."""

        def _uw(self, path, params):
            self.uw.set_background(True)
            try:
                res = self.uw.get(path, params)
            finally:
                self.uw.set_background(False)
            captured[(params or {}).get("ticker")] = {"integration_status": res.get("status"), "detail": res.get("detail"), "code": res.get("code"), "path": path, "params": dict(params or {})}
            return self._finish(res)

        @staticmethod
        def _finish(res):
            from smvshadow import governor as gv
            st = res.get("status")
            if st == "OK" and res.get("data") is not None:
                return json.dumps({"data": res["data"]}, default=str).encode("utf-8")
            if st in ("RATE_LIMITED", "BUDGET_EXHAUSTED"):
                raise gv.ProviderError("rate_limited", 429)
            if st in ("AUTH_ERROR", "PROVIDER_NOT_CONFIGURED", "UNAUTHORIZED"):
                raise gv.ProviderError("auth", 401)
            raise gv.ProviderError("transient", st)

    C.NEWS_MAX_PAGES = 1                                              # page 0 only: no follow-up request can be added
    cfg = L.LiveConfig(mode="LIVE", store_dir=out / "store", refresh_enabled=True, authorized_daily=MAX_REQUESTS, spacing_s=2.0, max_retries=0)
    svc = L.RefreshService(cfg, RecordingFetcher(), L.SystemClock())
    tasks = [t for t in C.plan(tickers, svc.store, svc.clock.now(), include_news=True) if t["kind"] == "uw_news"]
    assert len(tasks) == len(tickers) <= MAX_REQUESTS
    started = datetime.now(UTC)
    acc = C.run(svc, tasks, max_requests=MAX_REQUESTS, stopped=lambda: bool(svc.gov.stopped_until and svc.clock.now() < svc.gov.stopped_until))
    companies = dict(p.split("=", 1) for p in a.companies.split(";") if "=" in p)
    report = {"started_utc": started.isoformat(), "max_requests_authorized": MAX_REQUESTS, "tickers": tickers, "accounting": acc, "ledger_entries": len(svc.gov.ledger), "per_ticker": {},
              "credentials_in_report": False}
    for sym in tickers:
        recs = [r for r in svc.store.read(C.EVIDENCE) if r["symbol"] == sym and r["kind"] == "uw_news"]
        cap = captured.get(sym) or {}
        row = {"integration_status": cap.get("integration_status"), "request": {"path": cap.get("path"), "params": cap.get("params")}, "evidence_status": recs[-1]["status"] if recs else "NO_RECORD",
               "retrieved_at": recs[-1]["retrieved_at"] if recs else None}
        if recs and recs[-1]["status"] == "OK":
            data = json.loads(svc.store.get(recs[-1]["raw_sha256"])).get("data") or []
            at = datetime.fromisoformat(recs[-1]["retrieved_at"])
            stamps = []
            for h in data:
                try:
                    stamps.append(datetime.fromisoformat(str(h.get("created_at")).replace("Z", "+00:00")))
                except (TypeError, ValueError):
                    pass
            keys = [(h.get("created_at"), h.get("source"), str(h.get("headline", "")).strip().lower()) for h in data]
            cls = [NW.classify(h, sym, companies.get(sym), at) for h in data]
            row.update(headlines=len(data), timestamps_parsed=len(stamps), oldest=min(stamps).isoformat() if stamps else None, newest=max(stamps).isoformat() if stamps else None,
                       tagged_with_symbol=sum(1 for h in data if sym in (h.get("tickers") or [])), sentiment=dict(Counter(str(h.get("sentiment")) for h in data)),
                       sources=dict(Counter(str(h.get("source")) for h in data)), duplicates_in_page=len(keys) - len(set(keys)), major=sum(1 for h in data if h.get("is_major")),
                       fields=sorted({k for h in data for k in h}), full_page=len(data) >= 25, would_request_next_page=C.news_next_page(data, 25, 0, at) if False else None,
                       research_labels=dict(Counter(c["label"] for c in cls)),
                       rows=[{"published": h.get("created_at"), "source": h.get("source"), "headline": h.get("headline"), "tickers": h.get("tickers"), "provider_sentiment": h.get("sentiment"),
                              "research_label": c["label"], "category": c["category"], "reason": c["reason"]} for h, c in zip(data, cls)])
        report["per_ticker"][sym] = row
    f = out / "news_verify_report.json"
    f.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    print("requests attempted:", acc["attempted"], "ok:", acc["ok"], "failed:", acc["failed"], "stopped:", acc["stopped"], "->", f)
    return 0


if __name__ == "__main__":
    sys.exit(main())
