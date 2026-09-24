"""
Market scanner, run against the screener rather than inside TWS.

What changed and why
--------------------
The scanner this replaces asked TWS to rank the market with ``reqScannerData``
-- one request, ranked server-side, which was the right design when the
broker socket was the only feed here. With TWS gone the scan had nothing to
run on and the page answered ``PROVIDER_OFFLINE``.

The screener that replaces it is richer than the scan codes were. A scan row
arrived as a contract and a rank and had to be enriched with a second pass to
show a price; a screener row already carries the quote, the RSI, the moving
averages, the relative volume and the option tape. The ``technicals`` toggle
on the page therefore costs almost nothing now, and the rows are populated
whether or not it is on.

What the provider cannot rank, this module ranks itself -- always over one
request, never by asking per symbol. Where that is happening the row count it
was ranked over is stated, so nobody reads a locally sorted slice as the
whole market's order.
"""

from __future__ import annotations

from typing import Any, Optional

import live_market_service as market
import uw_screener_service as screener

SOURCE = "UNUSUAL_WHALES"
SCAN_TTL = 60.0

# How many rows to pull when the ranking is done here rather than by the
# provider. Wide enough that the top of a local sort is really the top.
LOCAL_POOL = 500

# Attaching bar-derived trend labels costs a history request per symbol.
MAX_ENRICH = 25


def _n(value: Any) -> Optional[float]:
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


def _above(row: dict, field: str) -> bool:
    close, line = _n(row.get("close")), _n(row.get(field))
    return bool(close and line and close > line)


def _below(row: dict, field: str) -> bool:
    close, line = _n(row.get("close")), _n(row.get(field))
    return bool(close and line and close < line)


def _ratio(row: dict, field: str) -> Optional[float]:
    close, mark = _n(row.get("close")), _n(row.get(field))
    return close / mark if close and mark else None


def _option_volume(row: dict) -> Optional[float]:
    calls, puts = _n(row.get("call_volume")), _n(row.get("put_volume"))
    return None if calls is None and puts is None else (calls or 0) + (puts or 0)


PRESETS: list[dict] = [
    {"key": "TOP_PERC_GAIN", "label": "Top % Gainers",
     "order": "one_day_perc", "direction": "desc"},
    {"key": "TOP_PERC_LOSE", "label": "Top % Losers",
     "order": "one_day_perc", "direction": "asc"},
    {"key": "MOST_ACTIVE", "label": "Most Active",
     "order": "stock_volume", "direction": "desc"},
    {"key": "HOT_BY_VOLUME", "label": "Hot by Volume",
     "order": "relative_volume", "direction": "desc"},
    {"key": "HIGH_OPT_VOLUME", "label": "High Option Volume",
     "order": "stock_volume", "direction": "desc",
     "rank_by": _option_volume},
    {"key": "HIGH_IV_RANK", "label": "Highest IV Rank",
     "order": "iv_rank", "direction": "desc"},
    {"key": "ABOVE_EMA20", "label": "Above EMA 20",
     "order": "stock_volume", "direction": "desc",
     "keep": lambda r: _above(r, "ema_20")},
    {"key": "ABOVE_EMA50", "label": "Above EMA 50",
     "order": "stock_volume", "direction": "desc",
     "keep": lambda r: _above(r, "ema_50")},
    # The screen carries no 200-day EMA, only the simple average, and the
    # label says which one it is rather than letting the old key imply the
    # other.
    {"key": "ABOVE_EMA200", "label": "Above SMA 200",
     "order": "stock_volume", "direction": "desc",
     "keep": lambda r: _above(r, "sma_200")},
    {"key": "BELOW_EMA20", "label": "Below EMA 20",
     "order": "stock_volume", "direction": "desc",
     "keep": lambda r: _below(r, "ema_20")},
    {"key": "HIGH_VS_52W", "label": "Near 52-Week High",
     "order": "stock_volume", "direction": "desc",
     "rank_by": lambda r: _ratio(r, "week_52_high")},
    {"key": "LOW_VS_52W", "label": "Near 52-Week Low",
     "order": "stock_volume", "direction": "desc",
     "rank_by": lambda r: _ratio(r, "week_52_low"), "invert": True},
]

PRESET_BY_KEY = {p["key"]: p for p in PRESETS}


def configured() -> bool:
    return screener.configured()


def list_presets() -> dict:
    """The scans on offer. Every one of them runs on the one feed."""
    available = configured()
    return {
        "presets": [
            {"key": p["key"], "label": p["label"], "available": available}
            for p in PRESETS
        ],
        "status": "OK" if available else "PROVIDER_NOT_CONFIGURED",
        "source": SOURCE,
    }


def scanner_status() -> dict:
    """Health entry: can the feed serve scans?"""
    if not configured():
        return {"status": "PROVIDER_NOT_CONFIGURED",
                "detail": "UNUSUAL_WHALES_API_KEY is not set."}
    return {"status": "OK", "presets": len(PRESETS),
            "detail": f"{len(PRESETS)} scans available from the screener."}


def _row(rank: int, r: dict) -> dict:
    """One screener row in the shape the scanner page reads."""
    close, prev = _n(r.get("close")), _n(r.get("prev_close"))
    # The provider states the day's move as a fraction.
    change_pct = _n(r.get("one_day_perc"))
    rsi, rvol = _n(r.get("rsi_14")), _n(r.get("relative_volume"))
    iv_rank = _n(r.get("iv_rank"))
    return {
        "rank": rank,
        "symbol": r.get("ticker"),
        "name": r.get("full_name"),
        # The screen is a market-wide table and does not name a venue. Said
        # as unknown rather than guessed from the ticker.
        "exchange": None,
        "price": round(close, 2) if close else None,
        "change": (round(close - prev, 2) if close and prev else None),
        "change_percent": (round(change_pct * 100, 2)
                           if change_pct is not None else None),
        "volume": _n(r.get("stock_volume")),
        "bid": _n(r.get("bid")),
        "ask": _n(r.get("ask")),
        "sector": r.get("sector"),
        "marketcap": _n(r.get("marketcap")),
        # Carried on the row itself, so these are populated without the
        # per-symbol history pass the old scanner needed for them.
        "rsi": round(rsi, 1) if rsi is not None else None,
        "relative_volume": round(rvol, 2) if rvol is not None else None,
        "above_ema20": _above(r, "ema_20") if _n(r.get("ema_20")) else None,
        "above_ema50": _above(r, "ema_50") if _n(r.get("ema_50")) else None,
        "above_ema200": _above(r, "sma_200") if _n(r.get("sma_200")) else None,
        "iv_rank": round(iv_rank, 1) if iv_rank is not None else None,
        "option_volume": _option_volume(r),
        "technicals_status": "OK",
        "source": SOURCE,
    }


def enrich_technicals(rows: list[dict]) -> list[dict]:
    """
    Attach the bar-derived trend label to the visible rows.

    RSI, the EMA posture and relative volume already arrive on the screener
    row; what the bars add is the trend state the Market Overview uses, which
    is a reading over the whole series rather than one line. Capped, because
    this is the only part of a scan that costs a request per symbol.
    """
    import market_overview_service as overview
    from live_market_service import get_batch

    symbols = [r["symbol"] for r in rows if r.get("symbol")][:MAX_ENRICH]
    if not symbols:
        return rows

    data = get_batch(symbols).get("symbols", {})
    for row in rows:
        hit = data.get(row.get("symbol"))
        if not hit:
            row.setdefault("trend", None)
            row["technicals_status"] = "NOT_SAMPLED"
            continue
        bars = hit.get("bars") or []
        trend = overview._trend(bars)
        # The screener's own readings stay as they are where it has them:
        # they are the figures the row was ranked on, and mixing two sources
        # inside one row makes the row incomparable with itself.
        for field in ("trend", "state", "ema20", "ema50", "ema200"):
            row[field] = trend.get(field)
        if row.get("rsi") is None:
            row["rsi"] = trend.get("rsi")
        row["technicals_status"] = (
            "OK" if len(bars) >= 60 else "INSUFFICIENT_HISTORY")
    return rows


def run_scan(
    preset: str = "MOST_ACTIVE",
    limit: int = 25,
    location: str = "STK.US.MAJOR",
    above_price: Optional[float] = None,
    below_price: Optional[float] = None,
    above_volume: Optional[int] = None,
    enrich: bool = True,
    technicals: bool = False,
) -> dict:
    """
    Run one scan.

    ``location`` is accepted and ignored: it named an IBKR scan universe, and
    the screen is US equities throughout. Kept so the page's existing request
    does not have to change.
    """
    spec = PRESET_BY_KEY.get(preset)
    if not spec:
        return {"rows": [], "status": "UNKNOWN_PRESET",
                "detail": f"No preset named {preset}", "source": SOURCE}

    if not configured():
        return {"rows": [], "status": "PROVIDER_NOT_CONFIGURED",
                "detail": "UNUSUAL_WHALES_API_KEY is not set.",
                "source": SOURCE}

    key = (f"scan:{preset}:{limit}:{above_price}:{below_price}:"
           f"{above_volume}:{technicals}")
    cached = market.cache.get(key, SCAN_TTL)
    if cached:
        return cached

    local = bool(spec.get("rank_by") or spec.get("keep"))
    filters: dict[str, Any] = {
        "order": spec["order"],
        "order_direction": spec["direction"],
    }
    if above_price is not None:
        filters["min_close"] = above_price
    if below_price is not None:
        filters["max_close"] = below_price
    if above_volume is not None:
        filters["min_volume"] = above_volume

    # A locally ranked preset has to see a wide slice before it sorts, or the
    # "top" of it is only the top of whatever the provider happened to send.
    out = screener.screen(LOCAL_POOL if local else limit, **filters)
    if out.get("status") not in ("OK", "NO_DATA"):
        return {"rows": [], "status": out.get("status"),
                "detail": out.get("detail"), "source": SOURCE}

    raw = out.get("rows") or []
    if spec.get("keep"):
        raw = [r for r in raw if spec["keep"](r)]
    if spec.get("rank_by"):
        ranked = [(spec["rank_by"](r), r) for r in raw]
        ranked = [(v, r) for v, r in ranked if v is not None]
        ranked.sort(key=lambda pair: pair[0], reverse=not spec.get("invert"))
        raw = [r for _v, r in ranked]

    rows = [_row(i + 1, r) for i, r in enumerate(raw[:limit])]

    if technicals and rows:
        try:
            rows = enrich_technicals(rows)
        except Exception:  # noqa: BLE001 - a scan is still useful without them
            for r in rows:
                r["technicals_status"] = "UNAVAILABLE"

    result = {
        "preset": preset,
        "label": spec["label"],
        "scan_code": spec["order"],
        "location": "US equities",
        "rows": rows,
        "count": len(rows),
        "enriched": len(rows),
        "technicals": bool(technicals),
        "status": "OK" if rows else "DATA_UNAVAILABLE",
        "note": (
            f"Ranked here over the {LOCAL_POOL} most active names; the screen "
            "does not sort on this itself."
            if local else
            "Ranked by the screener across every optionable US name."
        ),
        "source": SOURCE,
    }
    market.cache.put(key, result)
    return result
