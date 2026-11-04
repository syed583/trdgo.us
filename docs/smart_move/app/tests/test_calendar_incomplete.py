"""Found in the 2026-11-04 live pilot: when the calendar request budget ran out inside or before a slot, `calendar_truncated` stayed empty, so an incomplete calendar looked complete (4 Nov after-close returned two FULL pages and
was never marked). The new `calendar_incomplete` record says so. It is record-only: which requests are made, in what order, and what is stored are unchanged."""
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "docs" / "smart_move" / "app"))
import smart_move_live as L  # noqa: E402

from test_live import make  # noqa: E402

UTC = timezone.utc
NOW = datetime(2026, 11, 4, 16, 56, tzinfo=UTC)
SLOTS = [(date(2026, 11, 4), "premarket"), (date(2026, 11, 4), "afterhours")]


class Pages(L.Fetcher):
    mode = "SIMULATION"

    def __init__(self, rows_for_page):
        self.rows_for_page, self.uw = rows_for_page, []

    def fetch(self, kind, symbol, params):
        if kind == "uw_calendar":
            self.uw.append((params["side"], params.get("page", 0)))
            n = self.rows_for_page(params["side"], params.get("page", 0))
            return json.dumps({"data": [{"symbol": "Q" + chr(65 + i % 26) + chr(65 + i // 26), "report_date": params["date"], "report_time": params["side"], "source": "company", "marketcap": "1"} for i in range(n)]}).encode()
        return b"Ticker,Earnings\n" if kind == "finviz_calendar" else json.dumps({"earningsCalendar": []}).encode()


def test_budget_exhausted_inside_and_before_slots_is_recorded(tmp_path):
    prov = Pages(lambda side, page: 100)                                   # every page full
    svc, _, _ = make(tmp_path, start=NOW, fetcher=prov)
    done = svc.refresh_calendar([6], dates=SLOTS)
    st = svc.store.state()
    assert done == 6 and [p for _, p in prov.uw] == [0, 1, 2, 3] and {s for s, _ in prov.uw} == {"premarket"}      # request behaviour unchanged: the first slot used the whole Unusual Whales budget
    assert st["calendar_truncated"] == []                                                                         # page-cap record unchanged
    assert st["calendar_incomplete"] == [{"date": "2026-11-04", "side": "premarket", "pages_fetched": 4, "reason": "BUDGET_EXHAUSTED_AFTER_FULL_PAGE"},
                                         {"date": "2026-11-04", "side": "afterhours", "pages_fetched": 0, "reason": "NOT_FETCHED_BUDGET_EXHAUSTED"}]


def test_a_complete_refresh_records_nothing_incomplete(tmp_path):
    prov = Pages(lambda side, page: 100 if page == 0 else 40)              # a full first page, a partial second: complete
    svc, _, _ = make(tmp_path, start=NOW, fetcher=prov)
    done = svc.refresh_calendar([8], dates=SLOTS)
    st = svc.store.state()
    assert done == 6 and sorted(prov.uw) == [("afterhours", 0), ("afterhours", 1), ("premarket", 0), ("premarket", 1)]
    assert st["calendar_incomplete"] == [] and st["calendar_truncated"] == []


def test_page_cap_truncation_is_still_recorded_the_old_way_and_not_as_incomplete(tmp_path):
    prov = Pages(lambda side, page: 100)
    svc, _, _ = make(tmp_path, start=NOW, fetcher=prov, calendar_max_pages=2)
    svc.refresh_calendar([30], dates=[SLOTS[1]])
    st = svc.store.state()
    assert st["calendar_truncated"] == [{"date": "2026-11-04", "side": "afterhours", "pages": 2}] and st["calendar_incomplete"] == []
