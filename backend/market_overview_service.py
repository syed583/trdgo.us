"""
Market Overview: broad indices plus the eleven SPDR sector ETFs.

Everything comes from one batched IBKR pass (see live_market_service.get_batch)
rather than a request per card, and the technical read reuses the existing
technical_service calculations.
"""

from __future__ import annotations

from typing import Any, Optional

import live_market_service as market
from technical_service import calculate_technicals

INDICES = [
    {"symbol": "SPY", "label": "S&P 500", "note": "SPDR S&P 500 ETF"},
    {"symbol": "QQQ", "label": "Nasdaq 100", "note": "Invesco QQQ Trust"},
    {"symbol": "DIA", "label": "Dow Jones", "note": "SPDR Dow Jones ETF"},
    {"symbol": "IWM", "label": "Russell 2000", "note": "iShares Russell 2000"},
]

SECTORS = [
    {"symbol": "XLK", "label": "Technology"},
    {"symbol": "XLF", "label": "Financials"},
    {"symbol": "XLE", "label": "Energy"},
    {"symbol": "XLV", "label": "Health Care"},
    {"symbol": "XLY", "label": "Cons. Discretionary"},
    {"symbol": "XLP", "label": "Cons. Staples"},
    {"symbol": "XLI", "label": "Industrials"},
    {"symbol": "XLB", "label": "Materials"},
    {"symbol": "XLU", "label": "Utilities"},
    {"symbol": "XLRE", "label": "Real Estate"},
    {"symbol": "XLC", "label": "Communication Svcs"},
]

OVERVIEW_TTL_OPEN = 45.0
OVERVIEW_TTL_CLOSED = 900.0


def _trend(bars: list[dict]) -> dict:
    """Trend label and EMA posture from the existing technical service."""
    if len(bars) < 60:
        return {"trend": None, "state": "INSUFFICIENT_DATA",
                "above_ema20": None, "above_ema50": None, "above_ema200": None,
                "rsi": None}

    tech = calculate_technicals(bars) or {}
    close = bars[-1]["close"]
    e20, e50, e200 = tech.get("ema_20"), tech.get("ema_50"), tech.get("ema_200")

    above = [close > e for e in (e20, e50, e200) if e]
    score = sum(1 for a in above if a)

    if not above:
        state = "INSUFFICIENT_DATA"
    elif score == len(above) and e20 and e50 and e20 > e50:
        state = "STRONG_UPTREND"
    elif score >= 2:
        state = "UPTREND"
    elif score == 1:
        state = "MIXED"
    else:
        state = "DOWNTREND"

    return {
        "trend": state.replace("_", " ").title() if state != "INSUFFICIENT_DATA" else None,
        "state": state,
        "above_ema20": (close > e20) if e20 else None,
        "above_ema50": (close > e50) if e50 else None,
        "above_ema200": (close > e200) if e200 else None,
        "rsi": round(tech["rsi_14"], 1) if tech.get("rsi_14") is not None else None,
        "ema20": round(e20, 2) if e20 else None,
        "ema50": round(e50, 2) if e50 else None,
        "ema200": round(e200, 2) if e200 else None,
    }


def _card(spec: dict, row: dict) -> dict:
    bars = row.get("bars") or []
    return {
        "symbol": spec["symbol"],
        "label": spec["label"],
        "note": spec.get("note"),
        "price": row.get("price"),
        "change": row.get("change"),
        "change_percent": row.get("change_percent"),
        "status": row.get("status", "DATA_UNAVAILABLE"),
        **_trend(bars),
        "spark": [b["close"] for b in bars[-40:]],
    }


def get_market_overview() -> dict:
    ttl = market.session_ttl(OVERVIEW_TTL_OPEN, OVERVIEW_TTL_CLOSED)
    cached = market.cache.get("market_overview", ttl)
    if cached:
        return cached

    symbols = [s["symbol"] for s in INDICES] + [s["symbol"] for s in SECTORS]

    # The two calls do not need each other, and this page is the first thing
    # the app opens with: run them together rather than adding their waits.
    from concurrent.futures import ThreadPoolExecutor

    pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="overview")
    try:
        batch_f = pool.submit(market.get_batch, symbols)
        indices_f = pool.submit(market.get_indices)
        batch = batch_f.result()
        try:
            idx = indices_f.result()
        except Exception:  # noqa: BLE001
            idx = {}
    finally:
        pool.shutdown(wait=False)

    rows = batch.get("symbols", {})

    indices = [_card(spec, rows.get(spec["symbol"], {})) for spec in INDICES]
    sectors = [_card(spec, rows.get(spec["symbol"], {})) for spec in SECTORS]

    # VIX is an index rather than an ETF, so it comes from the indices feed.
    vix = next((i for i in idx.get("indices", []) if i["label"] == "VIX"), None)

    ranked = sorted(
        [s for s in sectors if s.get("change_percent") is not None],
        key=lambda s: s["change_percent"],
        reverse=True,
    )

    advancing = sum(1 for s in sectors if (s.get("change_percent") or 0) > 0)
    measured = sum(1 for s in sectors if s.get("change_percent") is not None)

    result = {
        "indices": indices,
        "sectors": sectors,
        "vix": vix,
        "leaders": ranked[:3],
        "laggards": ranked[-3:][::-1] if len(ranked) >= 3 else [],
        "breadth": {
            "advancing": advancing,
            "declining": measured - advancing,
            "measured": measured,
            "total": len(sectors),
        },
        "market": market.market_clock(),
        "status": batch.get("status", "OK"),
        "source": "UNUSUAL_WHALES",
    }
    market.cache.put("market_overview", result)
    return result
