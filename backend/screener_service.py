"""
The covered-stocks list: every optionable name the provider's screener tracks,
with a live price -- in a single request.

The screener row already carries the day's close, intraday change, market cap,
volume and IV rank, so the whole list comes from one call. That is deliberate:
pulling a quote per symbol for ~500 names is exactly what trips the provider's
rate limit, and this avoids it entirely.
"""

from __future__ import annotations

from typing import Any, Optional


def _f(v: Any) -> Optional[float]:
    try:
        return None if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return None


def get_all_stocks() -> dict:
    import unusualwhales_service as uw

    try:
        rows = uw._rows(uw.get("/api/screener/stocks", {"limit": 500}))
    except Exception:  # noqa: BLE001
        rows = []
    if not rows:
        return {"status": "NO_DATA", "rows": [], "count": 0,
                "detail": "The screener feed did not answer."}

    out = []
    for r in rows:
        sym = str(r.get("ticker") or r.get("symbol") or "").upper()
        if not sym:
            continue
        chg = _f(r.get("intraday_change"))
        out.append({
            "symbol": sym,
            "name": r.get("full_name") or sym,
            "price": _f(r.get("close")),
            "change_percent": round(chg * 100, 2) if chg is not None else None,
            "market_cap": _f(r.get("marketcap")),
            "volume": _f(r.get("stock_volume")),
            "iv_rank": round(_f(r.get("iv_rank")), 1) if _f(r.get("iv_rank")) is not None else None,
            "has_options": bool(r.get("has_options")),
            "sector": r.get("sector"),
            "week_52_high": _f(r.get("week_52_high")),
            "week_52_low": _f(r.get("week_52_low")),
        })
    # Biggest companies first; unknown caps sink to the bottom.
    out.sort(key=lambda x: (x["market_cap"] if x["market_cap"] is not None else -1),
             reverse=True)
    return {
        "status": "OK",
        "rows": out,
        "count": len(out),
        "detail": (f"{len(out)} optionable names from the provider's screener, "
                   "each with its last price -- fetched in one request."),
        "source": "UNUSUAL_WHALES",
    }
