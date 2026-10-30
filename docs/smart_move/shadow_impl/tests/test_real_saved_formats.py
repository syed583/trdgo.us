"""Adapters against REAL saved payloads (not in Git). Skipped unless the folders are given: SMV_VERIFY_RAW (the AAPL V1-V8 .json.gz files) and SMV_CANDLES (the saved candle files)."""
import gzip
import json
import os
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from smvshadow import adapters as ad, cutoff as co

RAW, CAND = os.environ.get("SMV_VERIFY_RAW", ""), os.environ.get("SMV_CANDLES", "")


@pytest.mark.skipif(not RAW or not Path(RAW).exists(), reason="SMV_VERIFY_RAW not set")
def test_f7_adapter_reads_the_real_volatility_context_payload():
    ctx = json.loads(gzip.open(Path(RAW) / "10_V6a.json.gz").read())
    data = ctx["data"]
    assert data["date"] == "2025-10-01" and data["skew"]["label"] == "call_rich" and "earnings_date" in data and "earnings_report_time" in data      # real shape, incl. a dated next-earnings record
    plan = co.plan_event(date(2025, 10, 3), "AMC")
    capture = plan["window_start_utc"].replace(hour=22, minute=0)                                                                                 # 18:00 ET on 1 Oct, after the 1 Oct close
    assert co.last_completed_session_at(capture) == date(2025, 10, 1)
    ev = ad.f7_volatility_context(ctx, None, plan, capture, "a" * 64)
    assert ev.status == "OK" and ev.extracted["skew_label"] == "call_rich" and ev.as_of_quality == "DATED_NOT_STAMPED"


@pytest.mark.skipif(not CAND or not (Path(CAND) / "AAPL.json").exists() and not (Path(CAND) / "ATR.json").exists(), reason="SMV_CANDLES not set")
def test_f5_f8_adapters_read_real_saved_candles():
    sym = "ATR"
    rows = json.loads((Path(CAND) / (sym + ".json")).read_text())
    spy = json.loads((Path(CAND) / "SPY.json").read_text())
    plan = co.plan_event(date(2025, 6, 12), "BMO")
    cap = plan["window_start_utc"].replace(hour=15)
    last = co.last_completed_session_at(cap)
    keep = lambda rs: [r for r in rs if r["date"][:10] <= last.isoformat()]
    f5 = ad.f5_price_technical(keep(rows), keep(spy), plan, cap, set(), "b" * 64)
    f8 = ad.f8_sector_market(keep(spy), None, plan, cap, "c" * 64)
    assert f5.status == "OK" and f5.usable and f5.extracted["adv20"] > 0 and f8.usable and f8.extracted["spy_ret20"] is not None
