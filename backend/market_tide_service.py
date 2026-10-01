"""
Market Tide: market-wide net option premium through the session.

Unusual Whales' market-tide feed sums net call and net put premium across the
whole options market, minute by minute -- a live read on whether money is
leaning bullish or bearish across the tape. We overlay SPY's price so the two
can be read together: a sustained rise in net-call / fall in net-put premium
tends to pull SPY up, and sharp reversals in the premium lines mark turns.
"""

from __future__ import annotations

from typing import Any, Optional


def _f(v: Any) -> Optional[float]:
    try:
        return None if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return None


def get_market_tide() -> dict:
    import unusualwhales_service as uw

    try:
        rows = uw._rows(uw.get("/api/market/market-tide", {"interval_5m": "false"}))
    except Exception:  # noqa: BLE001
        rows = []
    if not rows:
        return {"status": "NO_DATA", "series": [],
                "detail": "Market tide is not available right now."}

    # SPY minute price, keyed by HH:MM, for the overlay.
    spy_by_time: dict[str, float] = {}
    try:
        for b in uw._rows(uw.candles("SPY", size="1m", limit=450)):
            t = str(b.get("start_time") or "")
            # candles are UTC; the tide timestamps are ET. Match on the minute
            # by converting both to a HH:MM ET label below instead of raw UTC.
            c = _f(b.get("close"))
            if t and c is not None:
                spy_by_time[t[11:16]] = c  # UTC HH:MM, offset-corrected below
    except Exception:  # noqa: BLE001
        spy_by_time = {}

    # The tide timestamps carry the ET offset (…-04:00); SPY candles are UTC.
    # Convert the tide ET minute to the matching UTC HH:MM to line them up.
    from datetime import datetime

    def spy_price(ts: str) -> Optional[float]:
        try:
            dt = datetime.fromisoformat(ts)
            utc = dt.astimezone(tz=__import__("datetime").timezone.utc)
            return spy_by_time.get(utc.strftime("%H:%M"))
        except Exception:  # noqa: BLE001
            return None

    out = []
    for r in rows:
        ts = str(r.get("timestamp") or "")
        hm = ts[11:16] if len(ts) >= 16 else ts
        spy = spy_price(ts)
        # SPY tracks the S&P 500 at ~1/10; scale it so the overlay reads in index
        # points (e.g. 7,670) to match what people see quoted for the S&P 500.
        out.append({
            "time": hm,
            "net_call_premium": _f(r.get("net_call_premium")),
            "net_put_premium": _f(r.get("net_put_premium")),
            "net_volume": _f(r.get("net_volume")),
            "spy_price": spy,
            "sp500": round(spy * 10, 2) if spy is not None else None,
        })
    date = str(rows[0].get("date") or "")
    return {
        "status": "OK",
        "date": date,
        "series": out,
        "detail": ("Market-wide net call (green) and net put (red) premium by the "
                   "minute, with the S&P 500 level (gold), derived from SPY x10. "
                   "Rising net-call / falling net-put premium leans bullish."),
        "source": "UNUSUAL_WHALES",
    }
