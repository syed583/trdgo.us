"""Regression for the 2026-11-04 live pilot: with a small calendar budget the refresh spent every Unusual Whales request on the already-closed 4 Nov windows (oldest day first, two pages each) and never
reached the windows that were open, so none of the open events had a second-vendor-confirmable Unusual Whales row. Open windows must be fetched first, closed ones last."""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "docs" / "smart_move" / "app"))
import smart_move_live as L  # noqa: E402

from test_live import make  # noqa: E402

UTC = timezone.utc
NOW = datetime(2026, 11, 4, 17, 14, tzinfo=UTC)          # 12:14 ET: 5 Nov after-close and 6 Nov before-open are OPEN; every 4 Nov window and 5 Nov before-open are CLOSED


class Rec(L.Fetcher):
    mode = "SIMULATION"

    def __init__(self):
        self.uw = []

    def fetch(self, kind, symbol, params):
        if kind == "uw_calendar":
            self.uw.append((params["date"], params["side"], params.get("page", 0)))
            return json.dumps({"data": [{"symbol": "S" + chr(65 + i % 26) + chr(65 + i // 26), "report_date": params["date"], "report_time": params["side"], "source": "company", "marketcap": "1"} for i in range(100 if not params.get("page") else 10)]}).encode()
        if kind == "finviz_calendar":
            return b"Ticker,Earnings\n"
        return json.dumps({"earningsCalendar": []}).encode()


def test_open_windows_are_fetched_before_closed_ones_when_the_budget_is_small(tmp_path):
    rec = Rec()
    svc, _, _ = make(tmp_path, start=NOW, fetcher=rec)
    done = svc.refresh_calendar([6], days_ahead=3)                       # the pilot's budget: 4 Unusual Whales requests + the Finviz and Finnhub confirmations
    assert done == 6
    slots = [(d, s) for d, s, _ in rec.uw]
    assert slots[0] == ("2026-11-05", "afterhours")                      # open, nearest deadline (21:00Z today)
    assert ("2026-11-06", "premarket") in slots                          # open too
    assert not any(d == "2026-11-04" for d, _ in slots)                  # the closed slots are not paid for first


def test_a_large_budget_still_reaches_the_closed_slots_last(tmp_path):
    rec = Rec()
    svc, _, _ = make(tmp_path, start=NOW, fetcher=rec)
    svc.refresh_calendar([60], days_ahead=3)
    order = [(d, s) for d, s, p in rec.uw if p == 0]
    closed = {("2026-11-04", "premarket"), ("2026-11-04", "afterhours"), ("2026-11-05", "premarket")}
    assert set(order[:2]) == {("2026-11-05", "afterhours"), ("2026-11-06", "premarket")}      # open windows first
    assert set(order[-3:]) == closed and not closed & set(order[:-3])                          # closed windows last
