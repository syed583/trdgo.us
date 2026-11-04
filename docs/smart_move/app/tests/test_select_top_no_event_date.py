"""Found in the 2026-11-04 supervised LIVE probe: after the calendar refresh the runner selects again on the refreshed calendar, and a stock whose vendors disagree about the date by more than one day
(or whose only observations are stale vendor rows) has NO event date and therefore no `plan`. select_top read r["plan"] before checking whether the row was usable and crashed (KeyError 'plan'),
which stopped the whole run before any evidence request. Such a row must be skipped with its reason, never crash the selection, and never be selected."""
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "docs" / "smart_move" / "app"))
import smart_move_engine20 as E20  # noqa: E402
import smart_move_live as L  # noqa: E402

from test_live import make  # noqa: E402

UTC = timezone.utc
NOW = datetime(2026, 11, 4, 22, 30, tzinfo=UTC)
OBS_AT = "2026-11-04T22:20:00+00:00"


def obs(source, symbol, d, label=None, flag=None, cap=None):
    return {"source": source, "symbol": symbol, "date": d, "label": label, "clock": None, "vendor_flag": flag, "company": None, "sector": "Technology", "marketcap": cap, "observed_at": OBS_AT,
            "raw_sha256": (source[:2] + symbol).ljust(64, "0"), "request_id": "t-" + source + symbol, "mode": "SIMULATION"}


def test_a_row_without_an_event_date_is_skipped_not_a_crash(tmp_path):
    svc, _, _ = make(tmp_path, start=NOW)
    for r in (obs("UW_CALENDAR", "GOOD", "2026-11-06", "premarket", "company", "9e9"), obs("FINNHUB", "GOOD", "2026-11-06", "bmo"),            # confirmed, window open
              obs("FINNHUB", "CONF", "2026-11-04", "bmo"), obs("UW_CALENDAR", "CONF", "2026-11-09", "premarket", "company", "8e9")):          # vendors 5 days apart: no event date
        svc.store.append("cal_obs", r)
    rows = L.LiveScorer(svc).score_all(NOW)
    by = {r["symbol"]: r for r in rows}
    assert "plan" not in by["CONF"] and by["CONF"]["blocked_by"] == ["NO_EVENT_DATE"] and by["CONF"]["calendar"]["no_trade_reasons"] == ["DATE_CONFLICT_OVER_1_DAY"]      # the shape that used to crash the selection
    audit = []
    sel = E20.select_top(rows, {"GOOD": 9e9, "CONF": 8e9}, NOW, policy="deadline", pending={"GOOD", "CONF"}, audit=audit, n=20)
    assert [s["symbol"] for s in sel] == ["GOOD"]                                                               # the usable event is still selected
    skipped = [a for a in audit if a["symbol"] == "CONF"]
    assert skipped and skipped[0]["status"] == "SKIPPED" and "NO_EVENT_DATE" in skipped[0]["reason"] and "DATE_CONFLICT_OVER_1_DAY" in skipped[0]["reason"]  # and the conflicting one is explained, not silently dropped


def test_a_row_with_only_stale_vendor_rows_is_also_skipped(tmp_path):
    svc, _, _ = make(tmp_path, start=NOW)
    svc.store.append("cal_obs", obs("FINVIZ", "OLD", "2026-10-20"))                                              # a long-past date: a stale vendor row, no usable observation left
    rows = L.LiveScorer(svc).score_all(NOW)
    audit = []
    sel = E20.select_top(rows, {}, NOW, policy="deadline", pending={"OLD"}, audit=audit, n=20)
    assert sel == [] and any(a["symbol"] == "OLD" and a["status"] == "SKIPPED" for a in audit)
