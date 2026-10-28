"""Proposed (NOT applied): rows without a market_time label must never be guessed at.

The REAL AMKR rows below are the ones the production dry run printed on 2026-10-29. Strip their labels and the highest-volume
heuristic (what 9a242a58 does for unlabelled dates) picks the POST-market row on both days, because the provider's
total_volume of the post-market row (a running total) exceeds the regular row's. With the fail-closed rule the dates have
no regular close, so the event stays pending and is retried."""
import pathlib
import sys
from datetime import date

BACKEND = next(p / "backend" for p in pathlib.Path(__file__).resolve().parents if (p / "backend" / "earnings_outcomes.py").exists())
sys.path.insert(0, str(BACKEND))

import earnings_outcomes as eo   # noqa: E402


def r(day, mt, close, vol, total):
    return {"date": day, "market_time": mt, "close": str(close), "volume": vol, "total_volume": total}


AMKR = [r("2026-10-27", "pr", "38.5", 369808, 369808), r("2026-10-27", "po", "40.0424", 1292846, 12669733), r("2026-10-27", "r", "39.94", 12669733, 11376887),
        r("2026-10-26", "pr", "38.65", 403326, 403326), r("2026-10-26", "po", "38.3194", 4478299, 21618562), r("2026-10-26", "r", "36.81", 21618562, 17140263)]


def test_labelled_real_rows_pick_the_regular_close():
    assert eo.regular_session_closes(AMKR) == {date(2026, 10, 27): 39.94, date(2026, 10, 26): 36.81}


def test_unlabelled_real_rows_are_never_guessed():
    stripped = [{k: v for k, v in row.items() if k != "market_time"} for row in AMKR]
    assert eo.regular_session_closes(stripped) == {}                       # no regular close -> PENDING_PRICE, retried
    # the highest-total_volume guess would have chosen the post-market rows (40.0424 and 38.3194)
    guess = {}
    for d in ("2026-10-27", "2026-10-26"):
        guess[d] = max((x for x in stripped if x["date"] == d), key=lambda x: x["total_volume"])["close"]
    assert guess == {"2026-10-27": "40.0424", "2026-10-26": "38.3194"}


def test_partly_labelled_dates_use_only_the_regular_label():
    rows = [{"date": "2026-10-27", "close": "1"}, {"date": "2026-10-27", "close": "2", "market_time": "r"}, {"date": "2026-10-28", "close": "3", "market_time": "po"}]
    assert eo.regular_session_closes(rows) == {date(2026, 10, 27): 2.0}
