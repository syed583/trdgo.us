"""Extra intraday signals wired from our existing Unusual Whales integration,
to fill factors the live engine does not carry:

* market_breadth_bias()  -- market-wide net call vs put premium (UW market-tide).
  Cheap (one shared call), used on the board AND the detail view.
* enrich(symbol)         -- per-symbol GEX gamma-flip (options_levels) and net
  premium flow (net-prem-ticks). More UW calls, so used ONLY on the single-stock
  detail view, never in the ~38-name board scan (stays under the daily limit).

All biases are -1..+1. Everything is cached; failures return None (-> "no data").
"""
from __future__ import annotations

from typing import Optional

import claude_last_service as cl


def _clamp(x: Optional[float]) -> Optional[float]:
    return None if x is None else cl._clamp(x)


def market_breadth_bias() -> Optional[float]:
    """Market-wide tape lean from UW market-tide (net call minus net put premium
    at the latest point). Cached ~5 min; shared across every name."""
    import swr

    def _calc() -> Optional[float]:
        try:
            import market_tide_service as mt
            t = mt.get_market_tide()
            pts = (t or {}).get("points") or []
            if not pts:
                return None
            last = pts[-1]
            nc = cl._f(last.get("net_call_premium")) or 0.0
            npm = cl._f(last.get("net_put_premium")) or 0.0
            denom = abs(nc) + abs(npm)
            return _clamp((nc - npm) / denom) if denom else None
        except Exception:  # noqa: BLE001
            return None

    v = swr.serve("intraday:breadth", _calc, 300.0)
    return v if isinstance(v, (int, float)) else None


def _gamma_bias(symbol: str) -> Optional[float]:
    """Price vs dealer gamma-flip: above flip (positive gamma / supportive) is
    bullish, below is bearish. Scaled so ~1% past the flip is full credit."""
    try:
        import options_levels_service as ol
        lv = ol.get_levels(symbol) or {}
        flip = cl._f(lv.get("gamma_flip"))
        spot = cl._f(lv.get("spot")) or cl._f(lv.get("price"))
        if flip is None or spot is None or spot == 0:
            return None
        return _clamp(((spot - flip) / spot) / 0.01)
    except Exception:  # noqa: BLE001
        return None


def _netflow_bias(symbol: str) -> Optional[float]:
    """Session net options premium (calls minus puts) from net-prem-ticks."""
    try:
        import unusualwhales_service as uw
        d = uw.net_premium_ticks(symbol) or {}
        rows = d.get("rows") or d.get("data") or d.get("ticks") or []
        nc = sum((cl._f(r.get("net_call_premium")) or 0.0) for r in rows)
        npm = sum((cl._f(r.get("net_put_premium")) or 0.0) for r in rows)
        denom = abs(nc) + abs(npm)
        return _clamp((nc - npm) / denom) if denom else None
    except Exception:  # noqa: BLE001
        return None


def enrich(symbol: str) -> dict:
    """Per-symbol enrichment for the DETAIL view only (extra UW calls). Cached
    per symbol so repeated detail opens are cheap."""
    import swr
    sym = (symbol or "").upper()

    def _calc() -> dict:
        return {"gamma": _gamma_bias(sym), "order_flow": _netflow_bias(sym)}

    v = swr.serve(f"intraday:enrich:{sym}", _calc, 300.0)
    return v if isinstance(v, dict) else {}
