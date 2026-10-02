"""
OI Build-Up.

Open interest is the count of contracts that stay open overnight. When it rises,
positions are being *opened* and held -- which separates real accumulation from
a crowd trading in and out all day and closing flat. Unusual Whales publishes
the per-contract change once each trading day, around 6:45am ET in the premarket;
it is not an intraday figure.

This backend normalizes that feed into rows the screen can rank: biggest OI
increases (and decreases), the % change, how many sessions a contract has built
in a row, and where the day's volume ran past the open interest -- the classic
"new positions, not churn" tell.

Two scopes, same shape: market-wide (no symbol) and one ticker.
"""

from __future__ import annotations

from typing import Optional

SOURCE = "Unusual Whales"


def _f(v) -> Optional[float]:
    try:
        return None if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return None


def _i(v) -> Optional[int]:
    f = _f(v)
    return None if f is None else int(round(f))


def _row(raw: dict) -> dict:
    """One contract's overnight OI move, parsed and scaled for the table."""
    import contract_detail_service as cd

    occ = str(raw.get("option_symbol") or "")
    parsed = cd.parse_occ(occ) or {}
    curr_oi = _i(raw.get("curr_oi"))
    last_oi = _i(raw.get("last_oi"))
    diff = _i(raw.get("oi_diff_plain"))
    if diff is None and curr_oi is not None and last_oi is not None:
        diff = curr_oi - last_oi

    # Their `oi_change` is a ratio (0.21 = +21%, 15.6 = +1561%); make it a percent.
    ratio = _f(raw.get("oi_change"))
    change_pct = round(ratio * 100, 1) if ratio is not None else None

    volume = _i(raw.get("volume"))
    vol_oi = (round(volume / curr_oi, 2)
              if volume is not None and curr_oi else None)
    pct_total = _f(raw.get("percentage_of_total"))

    return {
        "option_symbol": occ,
        "symbol": (raw.get("underlying_symbol")
                   or parsed.get("symbol") or "").upper(),
        "expiry": parsed.get("expiry"),
        "strike": parsed.get("strike"),
        "right": parsed.get("right"),
        "type": ("Call" if parsed.get("right") == "C"
                 else "Put" if parsed.get("right") == "P" else None),
        "curr_oi": curr_oi,
        "prev_oi": last_oi,
        "oi_diff": diff,
        "oi_change_pct": change_pct,
        "volume": volume,
        "vol_oi_ratio": vol_oi,
        "trades": _i(raw.get("trades")),
        "avg_price": _f(raw.get("avg_price")),
        "last_fill": _f(raw.get("last_fill")),
        "premium": _f(raw.get("prev_total_premium")),
        "pct_of_total": round(pct_total * 100, 2) if pct_total is not None else None,
        # Only present on the market feed; passed through when the provider sends it.
        "days_building": _i(raw.get("days_of_oi_increases")),
        "days_vol_over_oi": _i(raw.get("days_of_vol_greater_than_oi")),
    }


def get_buildup(symbol: str = "", limit: int = 50, date: str = "") -> dict:
    """
    OI build-up rows, biggest overnight change first.

    Market-wide when ``symbol`` is empty, otherwise that one ticker. Rows are
    returned already ranked by absolute OI change (the provider's own order).
    """
    import unusualwhales_service as uw

    symbol = (symbol or "").upper().strip()
    limit = max(1, min(int(limit or 50), 200))

    out = (uw.oi_change(symbol, limit=limit, date=date) if symbol
           else uw.oi_change_market(limit=limit, date=date))
    if out.get("status") != "OK":
        return {"status": out.get("status", "NO_DATA"),
                "detail": out.get("detail"),
                "scope": "SYMBOL" if symbol else "MARKET",
                "symbol": symbol or None, "rows": [], "source": SOURCE}

    rows = [_row(r) for r in uw._rows(out)]
    rows = [r for r in rows if r["curr_oi"] is not None]

    gainers = [r for r in rows if (r["oi_diff"] or 0) > 0]
    losers = [r for r in rows if (r["oi_diff"] or 0) < 0]
    call_built = sum(r["oi_diff"] or 0 for r in gainers if r["right"] == "C")
    put_built = sum(r["oi_diff"] or 0 for r in gainers if r["right"] == "P")

    curr_date = next((r for r in uw._rows(out)), {}).get("curr_date")

    return {
        "status": "OK" if rows else "NO_DATA",
        "scope": "SYMBOL" if symbol else "MARKET",
        "symbol": symbol or None,
        "as_of": curr_date,
        "rows": rows,
        "summary": {
            "count": len(rows),
            "gainers": len(gainers),
            "losers": len(losers),
            "call_oi_added": int(call_built),
            "put_oi_added": int(put_built),
            # Which side the overnight building leans to, by contracts opened.
            "building": ("CALLS" if call_built > put_built
                         else "PUTS" if put_built > call_built else "BALANCED"),
        },
        "note": ("Open interest updates once per trading day (~6:45am ET). "
                 "These are positions held overnight, not intraday volume."),
        "source": SOURCE,
    }
