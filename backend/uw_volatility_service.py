"""
The volatility picture for one stock, from Unusual Whales.

Mirrors what their Volatility screen shows, from the endpoints the API plan
exposes:

  stats            IV rank, implied and realized vol, their 52-week highs and
                   lows, the variance risk premium and the front-expiry
                   implied move.
  iv_rv_series     A year of implied vs realized volatility with the IV rank
                   beside it -- the headline "is the option market pricing
                   more movement than the stock has delivered" chart.
  term_structure   Implied volatility and the implied move across every listed
                   expiry -- the curve from tomorrow out to the furthest date.

Every figure is the provider's. Volatilities arrive as fractions (0.235) and
are expressed here in the percent the screen talks in (23.5).
"""

from __future__ import annotations

from typing import Optional

import unusualwhales_service as uw

SOURCE = "UNUSUAL_WHALES"


def _pct(value) -> Optional[float]:
    """A fraction (0.235) as the percent the UI shows (23.5)."""
    f = uw._f(value)
    return round(f * 100, 2) if f is not None else None


def configured() -> bool:
    return uw.configured()


def _stats(symbol: str, front: Optional[dict]) -> dict:
    out = uw.get("/api/stock/%s/volatility/stats" % symbol)
    d = out.get("data") if out.get("status") == "OK" else None
    if isinstance(d, list):
        d = d[0] if d else None
    if not isinstance(d, dict):
        return {"status": out.get("status", "NO_DATA")}

    iv, rv = uw._f(d.get("iv")), uw._f(d.get("rv"))
    vrp = round(iv - rv, 4) if iv is not None and rv is not None else None
    return {
        "status": "OK",
        "iv_rank": round(uw._f(d.get("iv_rank")), 2) if d.get("iv_rank") else None,
        "iv": _pct(d.get("iv")),
        "rv": _pct(d.get("rv")),
        # Variance risk premium: how much dearer implied is than realized.
        "vrp": _pct(vrp) if vrp is not None else None,
        "iv_high": _pct(d.get("iv_high")),
        "iv_low": _pct(d.get("iv_low")),
        "rv_high": _pct(d.get("rv_high")),
        "rv_low": _pct(d.get("rv_low")),
        # Front-expiry implied move, the one the screen headlines.
        "implied_move_pct": _pct((front or {}).get("implied_move_perc")),
        "implied_move_dollars": (round(uw._f((front or {}).get("implied_move")), 2)
                                 if (front or {}).get("implied_move") else None),
        "as_of": d.get("date"),
    }


def _iv_rv_series(symbol: str) -> list[dict]:
    """A year of IV vs realized vol, with IV rank merged in by date."""
    realized = uw._rows(uw.get("/api/stock/%s/volatility/realized" % symbol,
                               {"limit": 400}))
    ranks = uw._rows(uw.get("/api/stock/%s/iv-rank" % symbol, {"limit": 400}))
    rank_by_date = {str(r.get("date"))[:10]: uw._f(r.get("iv_rank_1y"))
                    for r in ranks if r.get("date")}

    series = []
    for r in realized:
        date = str(r.get("date"))[:10]
        if not date:
            continue
        series.append({
            "date": date,
            "iv": _pct(r.get("implied_volatility")),
            "rv": _pct(r.get("realized_volatility")),
            "iv_rank": (round(rank_by_date[date], 2)
                        if date in rank_by_date and rank_by_date[date] is not None
                        else None),
        })
    series.sort(key=lambda x: x["date"])
    return series


def _term_structure(rows: list[dict]) -> list[dict]:
    """IV and the implied move across every listed expiry, near to far."""
    out = []
    for r in rows:
        out.append({
            "expiry": str(r.get("expiry"))[:10],
            "dte": r.get("dte"),
            "iv": _pct(r.get("volatility")),
            "implied_move_pct": _pct(r.get("implied_move_perc")),
        })
    out.sort(key=lambda x: (x.get("dte") if x.get("dte") is not None else 0))
    return out


def get_volatility(symbol: str) -> dict:
    """Everything the Volatility screen renders for one symbol."""
    symbol = (symbol or "").upper().strip()
    if not symbol:
        return {"status": "INVALID_SYMBOL", "source": SOURCE}
    if not configured():
        return {"status": "PROVIDER_NOT_CONFIGURED", "source": SOURCE}

    raw_term = uw._rows(uw.get("/api/stock/%s/volatility/term-structure" % symbol))
    term = _term_structure(raw_term)
    # The nearest expiry carries the headline implied move.
    front = raw_term[0] if raw_term else {}

    stats = _stats(symbol, front)
    series = _iv_rv_series(symbol)

    return {
        "symbol": symbol,
        "status": "OK" if (stats.get("status") == "OK" or series or term)
                  else "NO_DATA",
        "stats": stats,
        "iv_rv_series": series,
        "term_structure": term,
        "detail": ("Implied volatility is at-the-money, constant-maturity, and "
                   "refreshes through the session. Realized is the stock's own "
                   "recent movement."),
        "source": SOURCE,
    }
