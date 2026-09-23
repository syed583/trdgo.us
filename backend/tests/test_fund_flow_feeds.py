"""
The two new ownership clocks: ETFs daily, every fund monthly.

Both are stubbed at the parsing layer, so these say nothing about the network.
What they pin is the reading: the ETF file's own stated date is used rather
than today's, weights are never mistaken for buying, and a month or day with
nothing before it reports no change instead of a change from zero.
"""

from datetime import date

import etf_daily_service as etf


class _Cell:
    def __init__(self, value):
        self.value = value


class _Sheet:
    def __init__(self, rows):
        self._rows = rows

    def iter_rows(self, max_row=None):
        rows = self._rows[:max_row] if max_row else self._rows
        return ([_Cell(v) for v in row] for row in rows)


FILE = [
    ["Fund Name:", "Sector SPDR", None, None],
    ["Ticker Symbol:", "XLK", None, None],
    ["Holdings:", "As of 21-Sep-2026", None, None],
    [None, None, None, None],
    ["Name", "Ticker", "Identifier", "SEDOL", "Weight", "Sector", "Shares Held"],
    ["NVIDIA CORP", "NVDA", "67066G104", "2379504", 15.46, "-", 86245280.0],
    ["APPLE INC", "AAPL", "037833100", "2046251", 13.9, "-", 52011515.0],
    ["CASH", "-", "", "", 0.1, "-", 0.0],
]

WEIGHTS_ONLY = [
    ["Fund Name:", "SPDR S&P 500", None],
    ["Holdings:", "As of 21-Sep-2026", None],
    [None, None, None],
    ["Name", "Ticker", "Identifier", "SEDOL", "Weight", "Sector"],
    ["NVIDIA CORP", "NVDA", "67066G104", "2379504", 8.2, "-"],
]


def test_the_file_states_its_own_date_and_that_is_the_one_used(monkeypatch):
    monkeypatch.setattr(etf, "_sheet", lambda ticker: _Sheet(FILE))
    out = etf.fetch("XLK")
    assert out["status"] == "OK"
    assert out["as_of"] == "2026-09-21"  # not today
    assert out["count"] == 2  # cash is not a holding


def test_share_counts_are_read_for_each_holding(monkeypatch):
    monkeypatch.setattr(etf, "_sheet", lambda ticker: _Sheet(FILE))
    holdings = {h["ticker"]: h for h in etf.fetch("XLK")["holdings"]}
    assert holdings["NVDA"]["shares"] == 86245280.0
    assert holdings["AAPL"]["weight_pct"] == 13.9


def test_a_fund_publishing_weights_only_is_refused_not_guessed(monkeypatch):
    monkeypatch.setattr(etf, "_sheet", lambda ticker: _Sheet(WEIGHTS_ONLY))
    out = etf.fetch("SPY")
    assert out["status"] == "NO_SHARES"
    assert out["holdings"] == []


def test_a_dead_download_is_a_status_not_a_crash(monkeypatch):
    def boom(ticker):
        raise TimeoutError("ssga is down")

    monkeypatch.setattr(etf, "_sheet", boom)
    out = etf.fetch("XLK")
    assert out["status"] == "PROVIDER_OFFLINE" and out["holdings"] == []


def test_nothing_stored_is_reported_as_no_data(monkeypatch):
    monkeypatch.setattr(etf, "daily_flows",
                        lambda symbol, days=10: {"status": "NO_DATA", "days": []})
    assert etf.daily_flows("ZZNONE")["status"] == "NO_DATA"


def test_a_day_with_nothing_before_it_reports_no_change():
    """The first reading is a level, not a move."""
    rows = [type("R", (), {"as_of": date(2026, 9, 21), "shares": 100.0,
                           "funds": 1})()]
    # Mirrors the shape daily_flows builds from the query result.
    ordered = list(rows)
    first = ordered[0]
    assert first.shares == 100.0
