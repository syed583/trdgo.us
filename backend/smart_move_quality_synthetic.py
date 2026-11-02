"""SYNTHETIC example bundle for the evidence-quality page (development and tests only). Every symbol is invented (SYN1..SYN8), every number is made up, and the bundle carries synthetic=True and the label
"SYNTHETIC EXAMPLE DATA - not market data", which the page must show. It is produced by the REAL pipeline code (event records, pairing, news audit, outcome audit, accuracy) from fabricated inputs, so the page
renders every state the real data can produce. It is never written to a real report location by this module."""
from __future__ import annotations

import hashlib
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import smart_move_event_records as ER  # noqa: E402
import smart_move_price_bars as PB  # noqa: E402
import smart_move_quality_report as QR  # noqa: E402

UTC = timezone.utc
AS_OF = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)
NAMES = {"SYN%d" % i: n for i, n in enumerate(("ALPHA", "BRAVO", "CHARLIE", "DELTA", "ECHO", "FOXTROT", "GOLF"), 1)}
SHA = lambda s: hashlib.sha256(s.encode()).hexdigest()  # noqa: E731


def _cal(sym, date, at, label="postmarket", flag="company", fh="amc", both=True):
    rows = [{"source": "UW_CALENDAR", "symbol": sym, "date": date, "label": label, "clock": None, "vendor_flag": flag, "raw_sha256": SHA("u" + sym + date), "observed_at": at.isoformat(), "request_id": "syn", "mode": "SYNTHETIC",
             "company": "%s SYNTHETIC CORP" % NAMES[sym]}]
    if both:
        rows.append({"source": "FINNHUB", "symbol": sym, "date": date, "label": fh, "clock": None, "vendor_flag": None, "raw_sha256": SHA("f" + sym + date), "observed_at": at.isoformat(), "request_id": "syn", "mode": "SYNTHETIC"})
    return rows


def _event(rs, sym, date, obs_at, *, quarter="2026-09-30", payload_at=None, **kw):
    rows = _cal(sym, date, obs_at - timedelta(minutes=5), **kw)
    er = [{"source": "company", "report_date": date, "ending_fiscal_quarter": quarter, "report_time": "postmarket"}] if quarter else None
    rs.observe(ER.build_observation(sym, ER.calobs_from_rows(rows)[sym], obs_at, earnings_rows=er, earnings_retrieved_at=payload_at or obs_at - timedelta(hours=2), earnings_sha256=SHA("earn" + sym) if er else None))


def bundle() -> dict:
    rs = ER.EventRecords()
    d1, d2 = "2026-11-12", "2026-11-19"                       # AMC events; window opens 48 h before 21:00Z
    w1 = datetime(2026, 11, 10, 22, 0, tzinfo=UTC)
    w2 = datetime(2026, 11, 12, 20, 0, tzinfo=UTC)
    _event(rs, "SYN1", d1, w1)                                                         # eligible, quarter known, will be paired, verified outcome
    _event(rs, "SYN2", d1, w1, quarter=None)                                           # quarter unknown
    _event(rs, "SYN3", d1, w1, flag="estimation")                                      # estimated calendar: not eligible
    _event(rs, "SYN4", d1, w1)                                                         # eligible, bars show a split-shaped move (quarantine)
    _event(rs, "SYN5", d1, w1)                                                         # eligible, empty news response, no bars
    _event(rs, "SYN6", d2, w2)                                                         # future event: outcome pending
    _event(rs, "SYN7", d1, w1)                                                         # prediction captured after the cutoff
    ret = datetime(2026, 11, 13, 22, 0, tzinfo=UTC)
    bars = PB.BarStore()
    bars.ingest("SYN1", [{"date": "2026-11-12", "open": 100, "high": 101, "low": 99, "close": 100.0}, {"date": "2026-11-13", "open": 100, "high": 105, "low": 99.5, "close": 103.0}], source="synthetic fixture", retrieved_at=ret)
    bars.ingest("SYN4", [{"date": "2026-11-12", "open": 100, "high": 101, "low": 99, "close": 100.0}, {"date": "2026-11-13", "open": 50, "high": 51, "low": 49, "close": 50.0}], source="synthetic fixture", retrieved_at=ret)
    bars.ingest("SYN7", [{"date": "2026-11-12", "open": 100, "high": 101, "low": 99, "close": 100.0}, {"date": "2026-11-13", "open": 100, "high": 101, "low": 96, "close": 97.0}], source="synthetic fixture", retrieved_at=ret)
    cut = datetime(2026, 11, 11, 21, 0, tzinfo=UTC)
    pub = (cut - timedelta(hours=30)).isoformat().replace("+00:00", "Z")
    news = [{"record_id": "syn-n1", "symbol": "SYN1", "retrieved_at": (cut - timedelta(hours=8)).isoformat(), "raw_sha256": SHA("n1"), "status": "OK", "rows": 2,
             "headlines": [{"headline": "Alpha Synthetic Corp raises full-year outlook", "created_at": pub, "source": "GlobeNewswire", "sentiment": "neutral", "tickers": ["SYN1"]},
                           {"headline": "Chip makers rally on demand", "created_at": pub, "source": "GlobeNewswire", "sentiment": "neutral", "tickers": ["SYN1"]}]},
            {"record_id": "syn-n5", "symbol": "SYN5", "retrieved_at": (cut - timedelta(hours=8)).isoformat(), "raw_sha256": SHA("n5"), "status": "OK", "rows": 0, "headlines": []}]
    est = []
    rows_a = [{"horizon": "fiscal quarter", "date": "2026-09-30", "eps_estimate_average": "1.00", "revenue_estimate_average": "100"}]
    rows_b = [{"horizon": "fiscal quarter", "date": "2026-09-30", "eps_estimate_average": "1.10", "revenue_estimate_average": "104"}]
    payloads = {}
    for tag, at, rws in (("a", datetime(2026, 10, 27, 15, 0, tzinfo=UTC), rows_a), ("b", datetime(2026, 11, 10, 23, 0, tzinfo=UTC), rows_b)):
        sha = SHA("est-SYN1-" + tag)
        payloads[sha] = rws
        est.append({"kind": "uw_estimates", "symbol": "SYN1", "retrieved_at": at.isoformat(), "raw_sha256": sha, "status": "OK"})

    def pred(sym, at, sig):
        return {"symbol": sym, "earnings_date": d1, "captured_at": at.isoformat(), "digest": SHA("d" + sym), "version": 1, "engine": {"continuation": {"signal": sig}},
                "evidence": [{"kind": "ohlc", "symbol": sym, "retrieved_at": (at - timedelta(minutes=10)).isoformat(), "raw_sha256": SHA("o" + sym)}]}
    preds = [pred("SYN1", datetime(2026, 11, 11, 3, 0, tzinfo=UTC), "BUY"), pred("SYN4", datetime(2026, 11, 11, 3, 0, tzinfo=UTC), "SELL"), pred("SYN7", datetime(2026, 11, 12, 10, 0, tzinfo=UTC), "SELL")]
    return QR.build([rs], news_records=news, companies={k: "%s SYNTHETIC CORP" % v for k, v in NAMES.items()}, bars_by_symbol=bars.bars_by_symbol(), predictions=preds, estimate_recs=est,
                    load_estimate_rows=lambda sha: payloads.get(sha), as_of=AS_OF, source="SYNTHETIC_FIXTURE", synthetic=True,
                    notes=["All symbols, prices and headlines are invented for layout and state testing.", "Nothing on this bundle is market data."])
